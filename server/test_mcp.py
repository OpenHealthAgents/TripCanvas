import asyncio
import json
from main import call_tool
import mcp.types as types

async def test_plan_trip():
    print("Testing plan_trip tool...")
    arguments = {"destination": "NYC", "days": 2}
    result = await call_tool("plan_trip", arguments)
    
    print(f"Result type: {type(result)}")
    print(f"Result dict: {result.model_dump()}")
    
    if result.content:
        print(f"Text content: {result.content[0].text}")
        print("Success: Tool returned content, structured data, and meta template.")
    else:
        print("Failure: Tool did not return expected CallToolResult fields.")

if __name__ == "__main__":
    asyncio.run(test_plan_trip())
