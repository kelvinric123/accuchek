#!/usr/bin/env python3
"""
BLE (Bluetooth Low Energy) Listener Script
Connects to a BLE device and listens for all readings/notifications
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
        self.client = None
        
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
    
    def notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """Handle notifications/readings from BLE device"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        print(f"\n[{timestamp}] Notification from {sender.uuid}")
        print(f"  Characteristic UUID: {sender.uuid}")
        print(f"  Handle: {sender.handle}")
        print(f"  Data (hex): {data.hex()}")
        print(f"  Data (bytes): {data}")
        
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
        
        print("-" * 60)
    
    async def connect_and_listen(self):
        """Connect to BLE device and listen for notifications"""
        device = await self.scan_for_device()
        
        if device is None:
            print("Cannot proceed without a device.")
            return
        
        device_address = device.address if isinstance(device, type(device)) and hasattr(device, 'address') else device
        
        # Check pairing status before connecting
        self.check_pairing_status(device_address)
        
        print(f"\n{'='*60}")
        print(f"Connecting to device: {device_address}")
        print(f"{'='*60}\n")
        
        try:
            # Increase timeout for devices that need pairing
            async with BleakClient(device_address, timeout=30.0) as client:
                self.client = client
                
                print(f"✓ Connected successfully!")
                print(f"  Device: {client.address}")
                print(f"  MTU Size: {client.mtu_size}")
                print()
                
                # Get all services and characteristics
                print(f"{'='*60}")
                print("Device Services and Characteristics:")
                print(f"{'='*60}\n")
                
                services = await client.get_services()
                for service in services:
                    print(f"Service UUID: {service.uuid}")
                    print(f"  Description: {service.description}")
                    print(f"  Characteristics:")
                    
                    for char in service.characteristics:
                        props = []
                        if "read" in char.properties:
                            props.append("READ")
                        if "write" in char.properties:
                            props.append("WRITE")
                        if "notify" in char.properties:
                            props.append("NOTIFY")
                        if "indicate" in char.properties:
                            props.append("INDICATE")
                        
                        print(f"    - UUID: {char.uuid}")
                        print(f"      Handle: {char.handle}")
                        print(f"      Properties: {', '.join(props) if props else 'None'}")
                        
                        # Subscribe to notifications if available
                        if "notify" in char.properties:
                            try:
                                await client.start_notify(char.uuid, self.notification_handler)
                                print(f"      ✓ Subscribed to notifications")
                            except Exception as e:
                                print(f"      ✗ Failed to subscribe: {e}")
                        
                        # Subscribe to indications if available
                        if "indicate" in char.properties:
                            try:
                                await client.start_notify(char.uuid, self.notification_handler)
                                print(f"      ✓ Subscribed to indications")
                            except Exception as e:
                                print(f"      ✗ Failed to subscribe: {e}")
                    
                    print()
                
                print(f"{'='*60}")
                print("Listening for readings... (Press Ctrl+C to stop)")
                print(f"{'='*60}\n")
                
                # Keep the connection alive
                while True:
                    await asyncio.sleep(1)
                    
        except asyncio.CancelledError:
            print("\n\nConnection cancelled by user.")
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

