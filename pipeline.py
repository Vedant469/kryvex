from mcp.server import MCPServer

mcp = MCPServer("Kryvex")


@mcp.tool()
def hello(vedant: str) -> str:
    """Say hello to a user."""
    return f"Hello {vedant}! Kryvex MCP is working."


if __name__ == "__main__":
    mcp.run()