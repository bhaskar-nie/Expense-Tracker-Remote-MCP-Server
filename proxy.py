# from fastmcp import FastMCP

# # Create a proxy to your remote FastMCP Cloud server
# # FastMCP Cloud uses Streamable HTTP (default), so just use the /mcp URL
# mcp = FastMCP.as_proxy(
#     "https://Remote-Expense-Tracker-server.fastmcp.app/mcp",  # Standard FastMCP Cloud URL
#     name="Remote Expense Tracker Proxy"
# )

# if __name__ == "__main__":
#     # This runs via STDIO, which Claude Desktop can connect to
#     mcp.run()

# uv run fastmcp dev inspector proxy.py
# uv run fastmcp claude-desktop proxy.py 
# make changes in config json file and place uv path in config file, then restart claude desktop and connect to proxy server, then you can see the tools in claude desktop and test them out.