#!/usr/bin/env python3
"""
Glucose Meter (GM) Listener - Most Reliable Method
===================================================
Uses the standard Bluetooth Glucose Profile with RACP (Record Access Control Point)
to reliably retrieve glucose measurements from BLE glucose meters.

THE RELIABLE METHOD:
====================
This script uses the official Bluetooth Glucose Profile specification approach:

1. Connect to the glucose meter
2. Subscribe to notifications on:
   - Glucose Measurement (0x2a18)
   - Glucose Measurement Context (0x2a34) 
   - Record Access Control Point/RACP (0x2a52)
3. Write to RACP to REQUEST stored records
4. Device responds with glucose measurements
5. Device sends RACP response when complete

RACP COMMANDS:
==============
Write these to 0x2a52 to request data:
- 0x01 0x01: Report ALL stored records (most common)
- 0x01 0x06: Report NUMBER of stored records
- 0x04 0x01: Report FIRST record (oldest)
- 0x04 0x02: Report LAST record (newest)

SETUP:
======
1. Make sure device is paired and trusted:
   $ bluetoothctl
   $ pair 80:F5:B5:7F:99:0F
   $ trust 80:F5:B5:7F:99:0F
   $ exit

2. Activate your glucose meter (press button or take measurement)

3. Run this script:
   $ python3 gm_listener.py

4. Script will automatically request all stored records

DEVICE ATTRIBUTES (from your device):
=====================================
Service: 00001808-0000-1000-8000-00805f9b34fb (Glucose Service)
- Glucose Measurement (0x2a18) - Handle 0x0007 - NOTIFY
- Glucose Measurement Context (0x2a34) - Handle 0x000a - NOTIFY  
- Record Access Control Point (0x2a52) - Handle 0x000f - INDICATE/WRITE
- Glucose Feature (0x2a51) - Handle 0x000d - READ
- Date Time (0x2a08) - Handle 0x0012 - READ/WRITE
"""

import asyncio
import json
import os
from datetime import datetime
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

# Standard Bluetooth Glucose Service UUIDs
GLUCOSE_SERVICE_UUID = "00001808-0000-1000-8000-00805f9b34fb"
GLUCOSE_MEASUREMENT_UUID = "00002a18-0000-1000-8000-00805f9b34fb"
GLUCOSE_CONTEXT_UUID = "00002a34-0000-1000-8000-00805f9b34fb"
GLUCOSE_FEATURE_UUID = "00002a51-0000-1000-8000-00805f9b34fb"
RACP_UUID = "00002a52-0000-1000-8000-00805f9b34fb"
DATETIME_UUID = "00002a08-0000-1000-8000-00805f9b34fb"

# RACP Op Codes (write these to RACP characteristic)
RACP_OPCODE_REPORT_STORED_RECORDS = 0x01
RACP_OPCODE_DELETE_STORED_RECORDS = 0x02
RACP_OPCODE_ABORT_OPERATION = 0x03
RACP_OPCODE_REPORT_NUM_RECORDS = 0x04
RACP_OPCODE_NUM_RESPONSE = 0x05
RACP_OPCODE_RESPONSE_CODE = 0x06

# RACP Operators
RACP_OPERATOR_NULL = 0x00
RACP_OPERATOR_ALL_RECORDS = 0x01
RACP_OPERATOR_LESS_THAN_OR_EQUAL = 0x02
RACP_OPERATOR_GREATER_THAN_OR_EQUAL = 0x03
RACP_OPERATOR_WITHIN_RANGE = 0x04
RACP_OPERATOR_FIRST_RECORD = 0x05
RACP_OPERATOR_LAST_RECORD = 0x06


class GlucoseMeterListener:
    def __init__(self, config_file="config.json"):
        """Initialize the Glucose Meter listener"""
        self.config = self.load_config(config_file)
        self.device_address = self.config.get("mac_address")
        self.client = None
        self.measurements_received = []
        self.racp_response_received = False
        self.total_records = 0
        
        if not self.device_address:
            raise ValueError("MAC address not found in config file")
    
    def load_config(self, config_file):
        """Load configuration from JSON file"""
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file '{config_file}' not found")
        
        with open(config_file, 'r') as f:
            return json.load(f)
    
    def decode_glucose_measurement(self, data: bytearray):
        """Decode Glucose Measurement characteristic (0x2A18) per Bluetooth spec"""
        try:
            if len(data) < 10:
                return {"error": "Data too short", "raw": data.hex()}
            
            # Byte 0: Flags
            flags = data[0]
            time_offset_present = bool(flags & 0x01)
            concentration_units = "mmol/L" if (flags & 0x04) else "mg/dL"
            status_annunciation_present = bool(flags & 0x08)
            context_info_follows = bool(flags & 0x10)
            
            # Bytes 1-2: Sequence Number
            seq_num = int.from_bytes(data[1:3], byteorder='little')
            
            # Bytes 3-9: Base Time
            year = int.from_bytes(data[3:5], byteorder='little')
            month = data[5]
            day = data[6]
            hour = data[7]
            minute = data[8]
            second = data[9]
            timestamp = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
            
            offset = 10
            glucose_value = None
            sample_type = None
            sample_location = None
            
            # Time Offset (optional)
            time_offset = None
            if time_offset_present and len(data) >= offset + 2:
                time_offset = int.from_bytes(data[offset:offset+2], byteorder='little', signed=True)
                offset += 2
            
            # Glucose Concentration (3 bytes: SFLOAT + type/location)
            if len(data) >= offset + 3:
                # Decode SFLOAT (2 bytes)
                glucose_raw = int.from_bytes(data[offset:offset+2], byteorder='little')
                
                # Check for special values
                if glucose_raw == 0x07FF:
                    glucose_value = "NaN"
                elif glucose_raw == 0x0800:
                    glucose_value = "NRes"
                elif glucose_raw == 0x07FE:
                    glucose_value = "+INFINITY"
                elif glucose_raw == 0x0802:
                    glucose_value = "-INFINITY"
                elif glucose_raw == 0x0801:
                    glucose_value = "Reserved"
                else:
                    # Decode SFLOAT: 4-bit exponent, 12-bit mantissa
                    exponent = (glucose_raw >> 12) & 0x0F
                    if exponent >= 0x08:
                        exponent = -(0x10 - exponent)
                    mantissa = glucose_raw & 0x0FFF
                    if mantissa >= 0x0800:
                        mantissa = -(0x1000 - mantissa)
                    glucose_value = mantissa * (10 ** exponent)
                
                # Type and Location
                type_sample_location = data[offset + 2]
                sample_type = (type_sample_location >> 4) & 0x0F
                sample_location = type_sample_location & 0x0F
                offset += 3
            
            # Status Annunciation (optional)
            status = None
            if status_annunciation_present and len(data) >= offset + 2:
                status = int.from_bytes(data[offset:offset+2], byteorder='little')
            
            result = {
                "sequence_number": seq_num,
                "timestamp": timestamp,
                "glucose_value": glucose_value,
                "units": concentration_units,
                "sample_type": sample_type,
                "sample_location": sample_location,
                "time_offset_minutes": time_offset,
                "status": status,
                "context_follows": context_info_follows,
                "raw_hex": data.hex()
            }
            
            return result
            
        except Exception as e:
            return {"error": str(e), "raw": data.hex()}
    
    def decode_racp_response(self, data: bytearray):
        """Decode RACP (Record Access Control Point) response"""
        try:
            if len(data) < 2:
                return {"error": "Data too short", "raw": data.hex()}
            
            opcode = data[0]
            operator = data[1] if len(data) > 1 else None
            
            response = {
                "opcode": opcode,
                "operator": operator,
                "raw_hex": data.hex()
            }
            
            # Response Code (0x06)
            if opcode == RACP_OPCODE_RESPONSE_CODE:
                if len(data) >= 4:
                    request_opcode = data[2]
                    response_code = data[3]
                    
                    response_codes = {
                        0x01: "Success",
                        0x02: "Op Code Not Supported",
                        0x03: "Invalid Operator",
                        0x04: "Operator Not Supported",
                        0x05: "Invalid Operand",
                        0x06: "No Records Found",
                        0x07: "Abort Unsuccessful",
                        0x08: "Procedure Not Completed",
                        0x09: "Operand Not Supported"
                    }
                    
                    response["request_opcode"] = request_opcode
                    response["response_code"] = response_code
                    response["response_text"] = response_codes.get(response_code, "Unknown")
            
            # Number of Records Response (0x05)
            elif opcode == RACP_OPCODE_NUM_RESPONSE:
                if len(data) >= 4:
                    num_records = int.from_bytes(data[2:4], byteorder='little')
                    response["num_records"] = num_records
            
            return response
            
        except Exception as e:
            return {"error": str(e), "raw": data.hex()}
    
    def notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """Handle notifications from glucose meter"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        uuid = sender.uuid.lower()
        
        print(f"\n{'='*60}")
        print(f"📨 NOTIFICATION RECEIVED [{timestamp}]")
        print(f"{'='*60}")
        print(f"Characteristic: {sender.uuid}")
        print(f"Handle: {sender.handle}")
        print(f"Raw Data (hex): {data.hex()}")
        print(f"Raw Data (bytes): {list(data)}")
        
        # Glucose Measurement
        if uuid == GLUCOSE_MEASUREMENT_UUID:
            print(f"\n🩸 GLUCOSE MEASUREMENT:")
            decoded = self.decode_glucose_measurement(data)
            
            if "error" not in decoded:
                print(f"  Sequence #: {decoded['sequence_number']}")
                print(f"  Timestamp: {decoded['timestamp']}")
                print(f"  ⭐ GLUCOSE: {decoded['glucose_value']} {decoded['units']}")
                if decoded['sample_type'] is not None:
                    print(f"  Sample Type: {decoded['sample_type']}")
                if decoded['sample_location'] is not None:
                    print(f"  Sample Location: {decoded['sample_location']}")
                if decoded['time_offset_minutes'] is not None:
                    print(f"  Time Offset: {decoded['time_offset_minutes']} min")
                if decoded['status'] is not None:
                    print(f"  Status: 0x{decoded['status']:04x}")
                
                self.measurements_received.append(decoded)
            else:
                print(f"  ⚠ Decode Error: {decoded['error']}")
        
        # Glucose Measurement Context
        elif uuid == GLUCOSE_CONTEXT_UUID:
            print(f"\n📋 GLUCOSE MEASUREMENT CONTEXT:")
            print(f"  (Additional context data)")
            # Context decoding can be added if needed
        
        # RACP Response
        elif uuid == RACP_UUID:
            print(f"\n📝 RACP RESPONSE:")
            decoded = self.decode_racp_response(data)
            
            if "response_text" in decoded:
                print(f"  Response: {decoded['response_text']}")
                print(f"  Response Code: {decoded['response_code']}")
                if decoded['response_text'] == "Success":
                    print(f"  ✅ Operation completed successfully!")
                    self.racp_response_received = True
                elif decoded['response_text'] == "No Records Found":
                    print(f"  ℹ️  No glucose records stored on device")
                    self.racp_response_received = True
            elif "num_records" in decoded:
                print(f"  Number of Records: {decoded['num_records']}")
                self.total_records = decoded['num_records']
            else:
                print(f"  Opcode: 0x{decoded['opcode']:02x}")
                if decoded['operator'] is not None:
                    print(f"  Operator: 0x{decoded['operator']:02x}")
        
        else:
            print(f"\n📦 OTHER DATA:")
            # Try to decode as text
            try:
                text = data.decode('utf-8')
                print(f"  Text: {text}")
            except:
                pass
        
        print(f"{'='*60}\n")
    
    async def write_racp_command(self, opcode, operator):
        """Write command to RACP characteristic to request glucose records"""
        command = bytearray([opcode, operator])
        
        print(f"\n{'>'*60}")
        print(f"📤 WRITING RACP COMMAND")
        print(f"{'>'*60}")
        print(f"Characteristic: {RACP_UUID}")
        print(f"Command: {command.hex()} (OpCode: 0x{opcode:02x}, Operator: 0x{operator:02x})")
        
        if opcode == RACP_OPCODE_REPORT_STORED_RECORDS and operator == RACP_OPERATOR_ALL_RECORDS:
            print(f"Action: Request ALL stored glucose records")
        elif opcode == RACP_OPCODE_REPORT_NUM_RECORDS and operator == RACP_OPERATOR_ALL_RECORDS:
            print(f"Action: Request NUMBER of stored records")
        elif opcode == RACP_OPCODE_REPORT_STORED_RECORDS and operator == RACP_OPERATOR_FIRST_RECORD:
            print(f"Action: Request FIRST (oldest) record")
        elif opcode == RACP_OPCODE_REPORT_STORED_RECORDS and operator == RACP_OPERATOR_LAST_RECORD:
            print(f"Action: Request LAST (newest) record")
        
        try:
            await self.client.write_gatt_char(RACP_UUID, command, response=True)
            print(f"✅ Command sent successfully!")
            print(f"{'>'*60}\n")
            return True
        except Exception as e:
            print(f"❌ Failed to send command: {e}")
            print(f"{'>'*60}\n")
            return False
    
    async def connect_and_retrieve_data(self):
        """Connect to glucose meter and retrieve stored measurements"""
        print(f"\n{'#'*60}")
        print(f"  GLUCOSE METER LISTENER - Reliable RACP Method")
        print(f"{'#'*60}\n")
        
        print(f"Target Device: {self.device_address}")
        print(f"Method: Bluetooth Glucose Profile with RACP\n")
        
        # Scan for device
        print(f"{'='*60}")
        print("🔍 SCANNING FOR DEVICE...")
        print(f"{'='*60}\n")
        print("⚠️  IMPORTANT: Make sure your glucose meter is:")
        print("   • Powered on and awake")
        print("   • In pairing/active mode")
        print("   • Not connected to other devices\n")
        
        devices = await BleakScanner.discover(timeout=10)
        device_found = False
        
        for device in devices:
            if device.address.lower() == self.device_address.lower():
                print(f"✅ Device found: {device.name or 'Unknown'} ({device.address})")
                device_found = True
                break
        
        if not device_found:
            print(f"⚠️  Device not found in scan, attempting direct connection...")
        
        # Connect
        print(f"\n{'='*60}")
        print("🔗 CONNECTING TO DEVICE...")
        print(f"{'='*60}\n")
        
        try:
            async with BleakClient(self.device_address, timeout=30.0) as client:
                self.client = client
                
                print(f"✅ Connected successfully!")
                print(f"   Device: {client.address}")
                print(f"   Connected: {client.is_connected}\n")
                
                # Subscribe to characteristics
                print(f"{'='*60}")
                print("📡 SUBSCRIBING TO CHARACTERISTICS...")
                print(f"{'='*60}\n")
                
                subscribed = []
                
                # Subscribe to Glucose Measurement
                try:
                    print(f"Subscribing to Glucose Measurement (0x2a18)...", end=" ")
                    await client.start_notify(GLUCOSE_MEASUREMENT_UUID, self.notification_handler)
                    print("✅")
                    subscribed.append("Glucose Measurement")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"❌ ({e})")
                
                # Subscribe to Glucose Context (optional, may not be supported)
                try:
                    print(f"Subscribing to Glucose Context (0x2a34)...", end=" ")
                    await client.start_notify(GLUCOSE_CONTEXT_UUID, self.notification_handler)
                    print("✅")
                    subscribed.append("Glucose Context")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"⚠️  (Not available - OK, optional)")
                
                # Subscribe to RACP (REQUIRED for responses)
                try:
                    print(f"Subscribing to RACP (0x2a52)...", end=" ")
                    await client.start_notify(RACP_UUID, self.notification_handler)
                    print("✅")
                    subscribed.append("RACP")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"❌ ({e})")
                    print(f"\n❌ ERROR: RACP subscription failed. Cannot proceed.")
                    return
                
                print(f"\n✅ Subscribed to {len(subscribed)} characteristic(s)")
                print(f"   {', '.join(subscribed)}\n")
                
                # Wait a moment for subscriptions to stabilize
                await asyncio.sleep(1)
                
                # REQUEST STORED RECORDS via RACP - THIS IS THE KEY!
                print(f"{'='*60}")
                print("🚀 REQUESTING GLUCOSE RECORDS...")
                print(f"{'='*60}\n")
                
                # Optional: First ask how many records
                print("Step 1: Checking number of stored records...")
                await self.write_racp_command(RACP_OPCODE_REPORT_NUM_RECORDS, RACP_OPERATOR_ALL_RECORDS)
                await asyncio.sleep(2)
                
                # Request all stored records
                print("\nStep 2: Requesting all stored glucose records...")
                success = await self.write_racp_command(
                    RACP_OPCODE_REPORT_STORED_RECORDS,
                    RACP_OPERATOR_ALL_RECORDS
                )
                
                if not success:
                    print("\n❌ Failed to send RACP command")
                    return
                
                # Wait for data
                print(f"\n{'='*60}")
                print("⏳ WAITING FOR GLUCOSE DATA...")
                print(f"{'='*60}\n")
                print("Device should now send stored glucose measurements...")
                print("Waiting up to 30 seconds for data...\n")
                
                # Listen for responses (max 30 seconds)
                timeout = 30
                elapsed = 0
                
                while elapsed < timeout and not self.racp_response_received:
                    if not client.is_connected:
                        print(f"\n⚠️  Device disconnected after {elapsed}s")
                        break
                    
                    await asyncio.sleep(1)
                    elapsed += 1
                    
                    # Show progress every 5 seconds
                    if elapsed % 5 == 0 and elapsed < timeout:
                        print(f"[{elapsed}s] Still listening... ({len(self.measurements_received)} measurements received so far)")
                
                # Summary
                print(f"\n{'='*60}")
                print("📊 SUMMARY")
                print(f"{'='*60}\n")
                
                if len(self.measurements_received) > 0:
                    print(f"✅ SUCCESS! Received {len(self.measurements_received)} glucose measurement(s):\n")
                    
                    for i, measurement in enumerate(self.measurements_received, 1):
                        print(f"{i}. Seq #{measurement['sequence_number']}: "
                              f"{measurement['glucose_value']} {measurement['units']} "
                              f"at {measurement['timestamp']}")
                    
                    print(f"\n🎉 Glucose data successfully retrieved using RACP method!")
                else:
                    if self.racp_response_received:
                        print(f"ℹ️  No glucose measurements stored on device")
                        print(f"   Device responded successfully but has no records")
                        print(f"   Try taking a measurement first")
                    else:
                        print(f"⚠️  No glucose measurements received")
                        print(f"   Possible reasons:")
                        print(f"   - Device has no stored measurements")
                        print(f"   - Device needs time sync first")
                        print(f"   - Device requires authentication/pairing")
                        print(f"   - RACP command not supported by this device")
                
                print(f"\n{'='*60}\n")
                
                # Keep connection alive a bit longer
                print("Keeping connection alive for 10 more seconds...")
                print("(Take a measurement now if you want to test live data)\n")
                
                for i in range(10):
                    if not client.is_connected:
                        print(f"Device disconnected")
                        break
                    await asyncio.sleep(1)
                
        except asyncio.TimeoutError:
            print(f"\n❌ Connection timeout - device not responding")
            print(f"\nTroubleshooting:")
            print(f"  1. Make sure device is in active/pairing mode")
            print(f"  2. Try pairing with: bluetoothctl pair {self.device_address}")
            print(f"  3. Make sure device isn't connected elsewhere")
        
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print(f"   Type: {type(e).__name__}")
            
            print(f"\n{'='*60}")
            print("TROUBLESHOOTING:")
            print(f"{'='*60}")
            print(f"\n1. Ensure device is paired:")
            print(f"   bluetoothctl pair {self.device_address}")
            print(f"   bluetoothctl trust {self.device_address}")
            print(f"\n2. Make sure device is awake and in pairing mode")
            print(f"\n3. Check that device supports Glucose Service (0x1808)")
            print(f"\n4. Some devices require time sync before sending data")
            print(f"{'='*60}\n")


async def main():
    """Main entry point"""
    try:
        print("\n" + "="*60)
        print("  🩺 GLUCOSE METER LISTENER")
        print("  Using: RACP (Record Access Control Point) Method")
        print("  Most Reliable Standard Bluetooth Glucose Profile")
        print("="*60 + "\n")
        
        listener = GlucoseMeterListener()
        await listener.connect_and_retrieve_data()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        print("\nPlease create config.json with:")
        print('{\n  "mac_address": "80:F5:B5:7F:99:0F"\n}')
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        return 1
    
    print("\n✅ Listener finished\n")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

