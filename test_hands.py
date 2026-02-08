#!/usr/bin/env python3
"""Test script to verify Windows automation setup."""

import asyncio
import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_hands():
    """Test if hands can observe and act."""
    print("=" * 60)
    print("Testing Windows Automation Setup")
    print("=" * 60)
    
    # Test 1: Check environment
    print(f"\n1. Environment check:")
    print(f"   os.name: {os.name}")
    print(f"   MARVOS_HANDS: {os.environ.get('MARVOS_HANDS', 'not set (will use auto)')}")
    
    # Test 2: Import hands
    print(f"\n2. Importing hands adapter...")
    try:
        from app.hands.windows_mcp_adapter import WindowsMCPHands
        print("   ✓ WindowsMCPHands imported successfully")
    except Exception as e:
        print(f"   ✗ Failed to import WindowsMCPHands: {e}")
        return
    
    # Test 3: Initialize hands
    print(f"\n3. Initializing hands...")
    try:
        hands = WindowsMCPHands()
        print("   ✓ Hands initialized successfully")
        print(f"   Backend: {hands.__class__.__name__}")
    except Exception as e:
        print(f"   ✗ Failed to initialize hands: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Test 4: Observe
    print(f"\n4. Testing observe (with screenshot)...")
    try:
        obs = await hands.observe(include_screenshot=True, ui_tree_depth=5)
        print(f"   ✓ Observe succeeded")
        print(f"   Backend: {obs.get('backend')}")
        print(f"   Active window: {obs.get('active_window_title')}")
        print(f"   Windows count: {len(obs.get('windows', []))}")
        print(f"   Interactive elements: {obs.get('element_count', 0)}")
        if obs.get('error'):
            print(f"   ⚠ Error in observe: {obs['error']}")
        if obs.get('screenshot_base64_png'):
            screenshot_size = len(obs['screenshot_base64_png'])
            print(f"   Screenshot size: {screenshot_size} bytes")
    except Exception as e:
        print(f"   ✗ Observe failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Test 5: Test click on first element if available
    print(f"\n5. Testing click action...")
    elements = obs.get('interactive_elements', [])
    if elements:
        first_elem = elements[0]
        print(f"   Clicking element 0: {first_elem.get('name')} at coordinates {first_elem.get('coordinates')}")
        try:
            result = await hands.act(actions=[{
                "type": "click",
                "element_label": 0
            }])
            print(f"   ✓ Click succeeded: {result}")
        except Exception as e:
            print(f"   ✗ Click failed: {e}")
    else:
        print("   No interactive elements to click")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_hands())
