#!/usr/bin/env python3
"""
BLE (Bluetooth Low Energy) Listener Script for AccuChek Glucose Meters
Connects to a BLE device and requests glucose measurements

AUTOMATIC DATA RETRIEVAL (RACP):
=================================
This script automatically requests stored glucose measurements using RACP:
1. Connects to the AccuChek device
2. Subscribes to glucose measurement and RACP characteristics
3. Sends RACP command to request all stored records
4. Device sends back glucose measurements via notifications
5. Measurements are decoded and displayed

SETUP WORKFLOW FOR ACCUCHECK DEVICES:
======================================
1. Pair device once (one-time setup):
   $ bluetoothctl
   $ scan on
   $ pair 80:F5:B5:7F:99:0F
   $ trust 80:F5:B5:7F:99:0F
   $ exit

2. Configure config.json:
   {
     "mac_address": "80:F5:B5:7F:99:0F",
     "request_all_records": true,
     "subscribe_uuids": [
       "00002a18-0000-1000-8000-00805f9b34fb",  // Glucose Measurement
       "00002a34-0000-1000-8000-00805f9b34fb",  // Context
       "00002a52-0000-1000-8000-00805f9b34fb"   // RACP
     ]
   }

3. Activate your AccuCheck (wake it up):
   - Press the data transfer button on the device, OR
   - Access the Bluetooth menu on the device
   - Device should show "Data transfer" or Bluetooth icon

4. Run this script:
   $ python3 ble_listener.py

5. Script will:
   - Connect to device
   - Subscribe to characteristics
   - Request number of stored records
   - Request all stored records
   - Display glucose readings as they come in

ACCUCHECK GLUCOSE SERVICE UUIDS:
=================================
Based on Bluetooth Glucose Profile specification:

Service: 00001808-0000-1000-8000-00805f9b34fb (Glucose Service)

Important Characteristics:
- 00002a18: Glucose Measurement (NOTIFY) - Main glucose readings
- 00002a34: Glucose Measurement Context (NOTIFY) - Additional context
- 00002a52: Record Access Control Point (INDICATE/WRITE) - Request stored records
- 00002a51: Glucose Feature (READ) - Device capabilities
- 00002a08: Date Time (READ/WRITE) - Device time sync

RACP Commands (written to 00002a52):
- 0x01 0x01 = Report all stored records
- 0x04 0x01 = Report number of stored records
- 0x03 0x00 = Abort operation

TROUBLESHOOTING:
================
If no data is received:
- Make sure device is in "Data Transfer" mode (check device display)
- Check that all 3 UUIDs are in subscribe_uuids in config.json
- Set "request_all_records": true in config.json
- Device must have stored measurements to transfer
"""

import asyncio
import json
import os
import subprocess
from datetime import datetime
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData


class BLEListener:
    def __init__(self, config_file="config.json"):
        """Initialize the BLE listener with configuration"""
        self.config = self.load_config(config_file)
        self.device_address = self.config.get("mac_address")
        self.device_name = self.config.get("device_name", None)
        self.scan_timeout = self.config.get("scan_timeout", 10)
        self.wait_for_device = self.config.get("wait_for_device", True)
        self.discover_services = self.config.get("discover_services", False)
        self.subscribe_uuids = self.config.get("subscribe_uuids", [])
        self.minimal_mode = self.config.get("minimal_mode", True)
        self.request_all_records = self.config.get("request_all_records", False)
        self.client = None
        
        # RACP characteristic UUID
        self.RACP_UUID = "00002a52-0000-1000-8000-00805f9b34fb"
        
        if not self.device_address:
            raise ValueError("MAC address not found in config file")
    
    def load_config(self, config_file):
        """Load configuration from JSON file"""
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file '{config_file}' not found. Please create it.")
        
        with open(config_file, 'r') as f:
            return json.load(f)
    
    def check_pairing_status(self, device_address):
        """Check if device is already paired using bluetoothctl"""
        try:
            result = subprocess.run(
                ['bluetoothctl', 'info', device_address],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                output = result.stdout
                paired = "Paired: yes" in output
                trusted = "Trusted: yes" in output
                connected = "Connected: yes" in output
                
                print(f"\nDevice Pairing Status:")
                print(f"  Paired: {'✓ Yes' if paired else '✗ No'}")
                print(f"  Trusted: {'✓ Yes' if trusted else '✗ No'}")
                print(f"  Connected: {'✓ Yes' if connected else '✗ No'}")
                
                return paired, trusted, connected
            else:
                print(f"\nCouldn't retrieve pairing status (device may not be paired yet)")
                return False, False, False
                
        except FileNotFoundError:
            print(f"\nNote: bluetoothctl not found, skipping pairing status check")
            return None, None, None
        except Exception as e:
            print(f"\nNote: Could not check pairing status: {e}")
            return None, None, None
    
    async def scan_for_device(self):
        """Scan for BLE devices"""
        print(f"\n{'='*60}")
        print("Scanning for BLE devices...")
        print(f"{'='*60}\n")
        
        devices = await BleakScanner.discover(timeout=self.scan_timeout)
        
        if not devices:
            print("No devices found during scan.")
            return None
        
        print(f"Found {len(devices)} device(s):\n")
        for i, device in enumerate(devices, 1):
            print(f"{i}. Name: {device.name or 'Unknown'}")
            print(f"   Address: {device.address}")
            rssi = getattr(device, 'rssi', None)
            if rssi is not None:
                print(f"   RSSI: {rssi} dBm")
            else:
                print(f"   RSSI: Not available")
            print()
        
        # Try to find device by MAC address or name
        for device in devices:
            if device.address.lower() == self.device_address.lower():
                print(f"✓ Found target device: {device.name or 'Unknown'} ({device.address})")
                return device
            if self.device_name and device.name and self.device_name.lower() in device.name.lower():
                print(f"✓ Found target device by name: {device.name} ({device.address})")
                return device
        
        print(f"⚠ Warning: Device with MAC {self.device_address} not found in scan results.")
        print("Attempting to connect anyway...")
        return self.device_address
    
    def decode_glucose_measurement(self, data: bytearray):
        """Decode Glucose Measurement characteristic (0x2A18)"""
        try:
            if len(data) < 10:
                return "Data too short for glucose measurement"
            
            # Byte 0: Flags
            flags = data[0]
            time_offset_present = bool(flags & 0x01)
            concentration_and_type_present = bool(flags & 0x02)
            concentration_units = "mmol/L" if (flags & 0x04) else "mg/dL"
            status_annunciation_present = bool(flags & 0x08)
            context_info_follows = bool(flags & 0x10)
            
            result = []
            result.append(f"Flags: 0x{flags:02x}")
            result.append(f"  Units: {concentration_units}")
            result.append(f"  Context follows: {context_info_follows}")
            
            # Bytes 1-2: Sequence Number
            seq_num = int.from_bytes(data[1:3], byteorder='little')
            result.append(f"Sequence Number: {seq_num}")
            
            # Bytes 3-9: Base Time (year, month, day, hour, min, sec)
            year = int.from_bytes(data[3:5], byteorder='little')
            month = data[5]
            day = data[6]
            hour = data[7]
            minute = data[8]
            second = data[9]
            result.append(f"Timestamp: {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
            
            offset = 10
            
            # Time Offset (optional, 2 bytes)
            if time_offset_present and len(data) >= offset + 2:
                time_offset = int.from_bytes(data[offset:offset+2], byteorder='little', signed=True)
                result.append(f"Time Offset: {time_offset} minutes")
                offset += 2
            
            # Glucose Concentration (optional, 3 bytes: 2 for value, 1 for type/location)
            if concentration_and_type_present and len(data) >= offset + 3:
                # SFLOAT format (2 bytes)
                glucose_raw = int.from_bytes(data[offset:offset+2], byteorder='little')
                
                # Decode SFLOAT: 4-bit exponent, 12-bit mantissa
                exponent = (glucose_raw >> 12) & 0x0F
                if exponent >= 0x08:  # negative exponent
                    exponent = -(0x10 - exponent)
                mantissa = glucose_raw & 0x0FFF
                if mantissa >= 0x0800:  # negative mantissa
                    mantissa = -(0x1000 - mantissa)
                
                glucose_value = mantissa * (10 ** exponent)
                
                type_sample_location = data[offset + 2]
                sample_type = (type_sample_location >> 4) & 0x0F
                sample_location = type_sample_location & 0x0F
                
                result.append(f"🩸 GLUCOSE: {glucose_value} {concentration_units}")
                result.append(f"Sample Type: {sample_type}, Location: {sample_location}")
                offset += 3
            
            # Status Annunciation (optional, 2 bytes)
            if status_annunciation_present and len(data) >= offset + 2:
                status = int.from_bytes(data[offset:offset+2], byteorder='little')
                result.append(f"Status: 0x{status:04x}")
            
            return "\n  ".join(result)
        except Exception as e:
            return f"Decode error: {e}"
    
    def decode_racp_response(self, data: bytearray):
        """Decode Record Access Control Point (RACP) response"""
        try:
            if len(data) < 2:
                return "Data too short for RACP response"
            
            op_code = data[0]
            operator = data[1] if len(data) > 1 else 0
            
            op_codes = {
                1: "Report stored records",
                2: "Delete stored records",
                3: "Abort operation",
                4: "Report number of stored records",
                5: "Number of stored records response",
                6: "Response code"
            }
            
            result = []
            result.append(f"Op Code: {op_codes.get(op_code, f'Unknown ({op_code})')}")
            result.append(f"Operator: {operator}")
            
            # If it's a response code (op code 6)
            if op_code == 6 and len(data) >= 4:
                request_op_code = data[2]
                response_code_value = data[3]
                
                response_codes = {
                    1: "Success",
                    2: "Op code not supported",
                    3: "Invalid operator",
                    4: "Operator not supported",
                    5: "Invalid operand",
                    6: "No records found",
                    7: "Abort unsuccessful",
                    8: "Procedure not completed",
                    9: "Operand not supported"
                }
                
                result.append(f"Request Op Code: {op_codes.get(request_op_code, f'Unknown ({request_op_code})')}")
                result.append(f"Response: {response_codes.get(response_code_value, f'Unknown ({response_code_value})')}")
            
            # If it's number of records response (op code 5)
            elif op_code == 5 and len(data) >= 4:
                num_records = int.from_bytes(data[2:4], byteorder='little')
                result.append(f"📊 Number of stored records: {num_records}")
            
            return "\n  ".join(result)
        except Exception as e:
            return f"Decode error: {e}"
    
    def notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """Handle notifications/readings from BLE device"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        print(f"\n{'!'*60}")
        print(f"🔔 DATA RECEIVED!")
        print(f"{'!'*60}")
        print(f"[{timestamp}] Notification from {sender.uuid}")
        print(f"  Characteristic UUID: {sender.uuid}")
        print(f"  Handle: {sender.handle}")
        print(f"  Data (hex): {data.hex()}")
        print(f"  Data (bytes): {data}")
        print(f"  Data (raw): {list(data)}")
        
        # Try to decode as glucose measurement
        if sender.uuid.lower() == "00002a18-0000-1000-8000-00805f9b34fb":
            print(f"\n  📊 GLUCOSE MEASUREMENT DECODED:")
            decoded = self.decode_glucose_measurement(data)
            print(f"  {decoded}")
        
        # Try to decode as RACP response
        elif sender.uuid.lower() == "00002a52-0000-1000-8000-00805f9b34fb":
            print(f"\n  📋 RACP RESPONSE DECODED:")
            decoded = self.decode_racp_response(data)
            print(f"  {decoded}")
        
        # Try to decode as UTF-8 if possible
        try:
            text = data.decode('utf-8')
            print(f"  Data (text): {text}")
        except:
            pass
        
        # Try to decode as integers if it's small enough
        if len(data) <= 8:
            try:
                if len(data) == 1:
                    print(f"  Data (uint8): {int.from_bytes(data, byteorder='little')}")
                elif len(data) == 2:
                    print(f"  Data (uint16): {int.from_bytes(data, byteorder='little')}")
                elif len(data) == 4:
                    print(f"  Data (uint32): {int.from_bytes(data, byteorder='little')}")
                elif len(data) == 8:
                    print(f"  Data (uint64): {int.from_bytes(data, byteorder='little')}")
            except:
                pass
        
        print("!" * 60)
    
    async def request_all_stored_records(self, client):
        """Request all stored glucose records via RACP"""
        try:
            print(f"\n{'='*60}")
            print("📋 Requesting All Stored Records via RACP")
            print(f"{'='*60}\n")
            
            # RACP command: Report all stored records
            # Op Code: 0x01 (Report stored records)
            # Operator: 0x01 (All records)
            command = bytearray([0x01, 0x01])
            
            print(f"Writing RACP command: {command.hex()} (Report all stored records)")
            await client.write_gatt_char(self.RACP_UUID, command)
            print("✓ Command sent successfully!")
            print("  Waiting for device to send stored glucose measurements...\n")
            
            return True
        except Exception as e:
            print(f"✗ Failed to request records: {e}")
            return False
    
    async def request_number_of_records(self, client):
        """Request number of stored glucose records via RACP"""
        try:
            print(f"\n{'='*60}")
            print("📊 Requesting Number of Stored Records via RACP")
            print(f"{'='*60}\n")
            
            # RACP command: Report number of stored records
            # Op Code: 0x04 (Report number of stored records)
            # Operator: 0x01 (All records)
            command = bytearray([0x04, 0x01])
            
            print(f"Writing RACP command: {command.hex()} (Report number of records)")
            await client.write_gatt_char(self.RACP_UUID, command)
            print("✓ Command sent successfully!")
            print("  Waiting for response...\n")
            
            return True
        except Exception as e:
            print(f"✗ Failed to request number of records: {e}")
            return False
    
    async def wait_for_device_ready(self, device_address, max_attempts=10):
        """Wait for device to become discoverable/connectable"""
        print(f"\n{'='*60}")
        print("⏳ Waiting for device to become active...")
        print(f"{'='*60}\n")
        print("IMPORTANT: Make sure your AccuChek device is:")
        print("  • In pairing/transmission mode (follow device instructions)")
        print("  • Or actively taking a measurement")
        print("  • Device must be AWAKE to connect\n")
        
        for attempt in range(1, max_attempts + 1):
            print(f"Attempt {attempt}/{max_attempts}: Scanning for active device...")
            
            devices = await BleakScanner.discover(timeout=5)
            for device in devices:
                if device.address.lower() == device_address.lower():
                    print(f"✓ Device found and active: {device.name or 'Unknown'}")
                    return True
            
            if attempt < max_attempts:
                print(f"  Device not found yet, waiting 3 seconds...")
                print(f"  (Make sure device is in active/pairing mode!)\n")
                await asyncio.sleep(3)
        
        print(f"\n⚠ Device not found after {max_attempts} attempts")
        print("The device may be sleeping. Please activate it and try again.")
        return False
    
    async def connect_and_listen(self):
        """Connect to BLE device and listen for notifications"""
        device = await self.scan_for_device()
        
        if device is None:
            print("Cannot proceed without a device.")
            return
        
        device_address = device.address if isinstance(device, type(device)) and hasattr(device, 'address') else device
        
        # Check pairing status before connecting
        paired, trusted, connected = self.check_pairing_status(device_address)
        
        # If device is not currently connected/discoverable, wait for it
        if connected == False or paired == False:
            print(f"\n{'='*60}")
            print("⚠ DEVICE NOT CURRENTLY ACTIVE")
            print(f"{'='*60}")
            print("\nYour device needs to be AWAKE and in active mode to connect.")
            print("\nPlease do ONE of the following:")
            print("  1. Press the pairing/Bluetooth button on your AccuChek")
            print("  2. Start taking a measurement")
            print("  3. Access the device menu to keep it awake")
            print("\n📍 Activate your device NOW...")
            print(f"{'='*60}\n")
            
            # Give user time to activate device
            print("Waiting 5 seconds for you to activate the device...")
            await asyncio.sleep(5)
            
            # Wait for device to appear in scan
            if not await self.wait_for_device_ready(device_address):
                print("\n✗ Could not find active device. Exiting.")
                return
        
        print(f"\n{'='*60}")
        print(f"Connecting to device: {device_address}")
        print(f"{'='*60}\n")
        
        try:
            # Connect with minimal operations
            if self.minimal_mode:
                print("⚠ Using MINIMAL mode - connect once, minimal operations\n")
            
            async with BleakClient(device_address, timeout=30.0) as client:
                self.client = client
                
                print(f"✓ Connected successfully!")
                print(f"  Device: {client.address}")
                print(f"  Connected: {client.is_connected}")
                
                # If we have specific UUIDs to subscribe to, do it now
                subscribed_count = 0
                if self.subscribe_uuids and len(self.subscribe_uuids) > 0:
                    print(f"\nAttempting to subscribe to {len(self.subscribe_uuids)} UUID(s)...")
                    for uuid in self.subscribe_uuids:
                        try:
                            print(f"  Subscribing to {uuid}...", end=" ")
                            await client.start_notify(uuid, self.notification_handler)
                            print("✓")
                            subscribed_count += 1
                            await asyncio.sleep(0.2)
                        except Exception as e:
                            print(f"✗ ({e})")
                
                # If RACP is subscribed and request_all_records is enabled, request data
                if self.RACP_UUID in [u.lower() for u in self.subscribe_uuids] and self.request_all_records:
                    await asyncio.sleep(1)  # Give subscriptions time to settle
                    
                    # First, request number of records
                    await self.request_number_of_records(client)
                    await asyncio.sleep(2)  # Wait for response
                    
                    # Then request all records
                    await self.request_all_stored_records(client)
                    await asyncio.sleep(2)  # Give device time to prepare data
                
                print(f"\n{'='*60}")
                print("📡 LISTENING FOR DATA")
                print(f"{'='*60}\n")
                
                if subscribed_count > 0:
                    print(f"✓ Subscribed to {subscribed_count} characteristic(s)")
                    print("  Any data from subscribed characteristics will appear below.\n")
                    
                    if self.request_all_records and self.RACP_UUID in [u.lower() for u in self.subscribe_uuids]:
                        print("✓ RACP requests sent - waiting for glucose data...")
                        print("  Device will send stored measurements via notifications.\n")
                else:
                    print("⚠ No characteristics subscribed.")
                    print("  Connection is idle. Device may not send unsolicited data.\n")
                    print("  To subscribe to specific UUIDs, add them to config.json:")
                    print('    "subscribe_uuids": ["00002a18-...", "00002a52-..."]\n')
                
                print("Keeping connection alive. Press Ctrl+C to stop.")
                print(f"{'='*60}\n")
                
                # Just keep connection alive
                connection_time = 0
                last_status_time = 0
                
                try:
                    while True:
                        if not client.is_connected:
                            print(f"\n⚠ Device disconnected after {connection_time} seconds")
                            print(f"{'='*60}")
                            print(f"Connection lasted: {connection_time}s")
                            if connection_time < 5:
                                print("Very short connection - device likely rejecting connection")
                            elif connection_time < 30:
                                print("Connection dropped - device may have timed out")
                            else:
                                print("Connection held for a while - good sign!")
                            print(f"{'='*60}\n")
                            break
                        
                        # Show heartbeat every 10 seconds
                        if connection_time > 0 and connection_time % 10 == 0 and connection_time != last_status_time:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ Still connected ({connection_time}s)")
                            last_status_time = connection_time
                        
                        await asyncio.sleep(1)
                        connection_time += 1
                        
                except Exception as e:
                    print(f"\n⚠ Connection lost after {connection_time}s: {e}")
                    
        except asyncio.CancelledError:
            print("\n\nConnection cancelled by user.")
        except EOFError as e:
            print(f"\n\n✗ Connection Lost (EOFError): Device disconnected unexpectedly")
            print(f"\n{'='*60}")
            print("POSSIBLE CAUSES FOR ACCUCHECK DEVICES:")
            print(f"{'='*60}")
            print("\n1. Device has a very short connection timeout")
            print("   - AccuCheck may disconnect if idle for too long")
            print("   - Try taking a measurement immediately after connecting\n")
            print("2. Device requires time/date sync or specific handshake")
            print("   - Some glucose meters need to sync time first")
            print("   - May need to use manufacturer's app first\n")
            print("3. Device may be connected to another device/app")
            print("   - Make sure AccuCheck app is closed on phone")
            print("   - Make sure no other Bluetooth connections are active\n")
            print("4. Try this sequence:")
            print("   a. Put device in pairing mode")
            print("   b. Start this script")
            print("   c. IMMEDIATELY take a glucose measurement")
            print("   d. Device should stay connected during measurement\n")
            print(f"{'='*60}\n")
        except Exception as e:
            print(f"\n\n✗ Connection Error: {e}")
            print(f"Error Type: {type(e).__name__}")
            
            # Provide helpful troubleshooting info
            print(f"\n{'='*60}")
            print("TROUBLESHOOTING:")
            print(f"{'='*60}")
            print("\nIf you're getting pairing/authentication errors, try:")
            print("\n1. Remove existing pairing (if any):")
            print(f"   bluetoothctl")
            print(f"   remove {device_address}")
            print(f"   exit")
            print("\n2. Pair the device manually:")
            print(f"   bluetoothctl")
            print(f"   scan on")
            print(f"   (wait to see your device)")
            print(f"   scan off")
            print(f"   pair {device_address}")
            print(f"   (enter PIN if prompted)")
            print(f"   trust {device_address}")
            print(f"   exit")
            print("\n3. Then run this script again")
            print(f"\nOther common issues:")
            print("- Make sure the device is in pairing mode")
            print("- Make sure the device isn't connected to another device")
            print("- Try running with sudo if permission errors occur")
            print(f"{'='*60}\n")
            raise


async def main():
    """Main entry point"""
    try:
        listener = BLEListener()
        await listener.connect_and_listen()
    except KeyboardInterrupt:
        print("\n\nStopping listener...")
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())

