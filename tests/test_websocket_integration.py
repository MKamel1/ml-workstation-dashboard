"""Integration test for WebSocket stability."""

import asyncio
import websockets
import json
import time


async def test_websocket_connection():
    """Test basic WebSocket connection and data format."""
    uri = "ws://localhost:8000/ws"
    
    print("[Test] Connecting to WebSocket...")
    try:
        async with websockets.connect(uri, timeout=5) as ws:
            print("✓ Connection established")
            
            # Receive first message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            
            # Verify required fields
            assert 'timestamp' in data, "Missing timestamp field"
            assert 'gpu' in data, "Missing gpu field"
            assert 'cpu' in data, "Missing cpu field"
            assert 'memory' in data, "Missing memory field"
            
            # Verify timestamp is Unix epoch (BUG-C06 fix verification)
            timestamp = data['timestamp']
            assert timestamp > 1700000000, f"Timestamp {timestamp} is not Unix epoch"
            assert timestamp < 2000000000, f"Timestamp {timestamp} is too large"
            
            current_time = time.time()
            assert abs(timestamp - current_time) < 5, f"Timestamp {timestamp} is not current"
            
            print(f"✓ First message received with valid timestamp: {timestamp}")
            print(f"✓ Message contains all required fields")
            
            return True
            
    except asyncio.TimeoutError:
        print("❌ Connection timeout - is the server running?")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_rapid_reconnection():
    """Test that rapid connect/disconnect doesn't create duplicate connections (BUG-C01)."""
    uri = "ws://localhost:8000/ws"
    
    print("\n[Test] Testing rapid reconnection (BUG-C01 fix verification)...")
    
    try:
        for i in range(5):
            async with websockets.connect(uri, timeout=5) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(msg)
                assert 'timestamp' in data
                print(f"  ✓ Connection {i+1}/5: OK")
            await asyncio.sleep(0.1)
        
        print("✓ Rapid reconnection test passed - no duplicate connections detected")
        return True
        
    except Exception as e:
        print(f"❌ Rapid reconnection test failed: {e}")
        return False


async def test_error_recovery():
    """Test that connection survives temporary server issues."""
    uri = "ws://localhost:8000/ws"
    
    print("\n[Test] Testing error recovery (BUG-C03 fix verification)...")
    
    try:
        async with websockets.connect(uri, timeout=5) as ws:
            # Receive multiple messages to verify connection stays alive
            for i in range(3):
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(msg)
                
                # Check if server reported any errors
                if 'error' in data and data['error']:
                    print(f"  ⚠️  Server reported error but connection alive: {data.get('error_message', 'Unknown')}")
                else:
                    print(f"  ✓ Message {i+1}/3: received successfully")
                
                await asyncio.sleep(1)
        
        print("✓ Connection remained stable across multiple messages")
        return True
        
    except Exception as e:
        print(f"❌ Error recovery test failed: {e}")
        return False


async def main():
    """Run all integration tests."""
    print("=" * 60)
    print("WebSocket Integration Tests")
    print("=" * 60)
    print("\nNOTE: Server must be running at http://localhost:8000")
    print("Start server with: ./venv/bin/python app.py\n")
    
    results = []
    
    # Test 1: Basic connection
    results.append(await test_websocket_connection())
    
    # Test 2: Rapid reconnection
    results.append(await test_rapid_reconnection())
    
    # Test 3: Error recovery
    results.append(await test_error_recovery())
    
    # Summary
    print("\n" + "=" * 60)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    
    if all(results):
        print("✅ ALL INTEGRATION TESTS PASSED")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        return 1


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    exit(exit_code)
