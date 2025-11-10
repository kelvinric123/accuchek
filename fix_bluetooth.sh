#!/bin/bash
# Bluetooth Fix Script for BleakDBusError
# Run this to fix common Bluetooth/DBus issues on Linux

echo "=========================================="
echo "  Bluetooth/DBus Troubleshooting Script"
echo "=========================================="
echo ""

# 1. Check if Bluetooth service is running
echo "1. Checking Bluetooth service status..."
if systemctl is-active --quiet bluetooth; then
    echo "   ✓ Bluetooth service is running"
else
    echo "   ✗ Bluetooth service is NOT running"
    echo "   Attempting to start..."
    sudo systemctl start bluetooth
    sleep 2
    if systemctl is-active --quiet bluetooth; then
        echo "   ✓ Bluetooth service started successfully"
    else
        echo "   ✗ Failed to start Bluetooth service"
        echo "   Try: sudo systemctl status bluetooth"
    fi
fi
echo ""

# 2. Check if Bluetooth adapter is powered on
echo "2. Checking Bluetooth adapter power..."
hci_power=$(bluetoothctl show | grep "Powered" | awk '{print $2}')
if [ "$hci_power" == "yes" ]; then
    echo "   ✓ Bluetooth adapter is powered on"
else
    echo "   ✗ Bluetooth adapter is OFF"
    echo "   Turning on..."
    bluetoothctl power on
    sleep 1
    echo "   ✓ Power on command sent"
fi
echo ""

# 3. Check user permissions
echo "3. Checking user permissions..."
if groups | grep -q bluetooth; then
    echo "   ✓ User is in 'bluetooth' group"
else
    echo "   ✗ User is NOT in 'bluetooth' group"
    echo "   Adding user to bluetooth group..."
    sudo usermod -aG bluetooth $(whoami)
    echo "   ✓ User added to bluetooth group"
    echo "   ⚠  You need to LOG OUT and LOG BACK IN for this to take effect!"
fi
echo ""

# 4. Restart Bluetooth service
echo "4. Restarting Bluetooth service..."
sudo systemctl restart bluetooth
sleep 2
echo "   ✓ Bluetooth service restarted"
echo ""

# 5. Check DBus service
echo "5. Checking DBus service..."
if systemctl is-active --quiet dbus; then
    echo "   ✓ DBus service is running"
else
    echo "   ✗ DBus service is NOT running"
    sudo systemctl start dbus
    echo "   ✓ DBus service started"
fi
echo ""

# 6. Unblock Bluetooth (if blocked)
echo "6. Checking if Bluetooth is blocked..."
if rfkill list bluetooth | grep -q "Soft blocked: yes"; then
    echo "   ✗ Bluetooth is soft-blocked"
    echo "   Unblocking..."
    rfkill unblock bluetooth
    sleep 1
    echo "   ✓ Bluetooth unblocked"
elif rfkill list bluetooth | grep -q "Hard blocked: yes"; then
    echo "   ✗ Bluetooth is HARD-blocked (hardware switch)"
    echo "   ⚠  Check your laptop's physical Bluetooth switch!"
else
    echo "   ✓ Bluetooth is not blocked"
fi
echo ""

# 7. Check BlueZ version
echo "7. Checking BlueZ version..."
bluez_version=$(bluetoothctl --version 2>&1 | head -n1)
echo "   $bluez_version"
echo ""

# 8. Test basic bluetoothctl access
echo "8. Testing bluetoothctl access..."
if timeout 3 bluetoothctl list >/dev/null 2>&1; then
    echo "   ✓ bluetoothctl is accessible"
else
    echo "   ✗ bluetoothctl access failed"
    echo "   This might indicate a DBus permission issue"
fi
echo ""

echo "=========================================="
echo "  Troubleshooting Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. If you were added to bluetooth group, LOG OUT and LOG BACK IN"
echo "2. Try running your Python script again"
echo "3. If still failing, try with sudo: sudo python3 gm_listener.py"
echo ""
echo "To manually test Bluetooth:"
echo "  bluetoothctl"
echo "  power on"
echo "  scan on"
echo "  (wait a few seconds)"
echo "  scan off"
echo ""

