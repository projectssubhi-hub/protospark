import os
import psycopg2
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport

# 1. Initialize the MCP Server core
mcp_server = Server("secure-free-db-server")

# Fetch configuration safely from Environment Variables
DATABASE_URL = os.environ.get("DATABASE_URL")
MY_SECRET_ACCOUNT_KEY = os.environ.get("MY_SECRET_ACCOUNT_KEY")

def get_db_connection():
    global DATABASE_URL
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set.")
    
    # FIX FOR WINDOWS: Strip any literal double quotes added by the 'set' command
    cleaned_url = DATABASE_URL.strip('"')
    
    return psycopg2.connect(cleaned_url)

# 2. Register Database Tools
@mcp_server.list_tools()
async def handle_list_tools():
    return [
        Tool(
            name="read_query",
            description="Run SELECT queries on the database to read tables, rows, schema configurations, or statistics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The exact SELECT SQL statement to execute."}
                },
                "required": ["sql"]
            }
        ),
        Tool(
            name="write_and_edit_query",
            description="Run INSERT, UPDATE, DELETE, CREATE, or ALTER queries to modify rows or structural schema data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The exact structural or mutation SQL statement to execute."}
                },
                "required": ["sql"]
            }
        )
    ]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if name not in ["read_query", "write_and_edit_query"]:
        raise ValueError(f"Unknown tool: {name}")
        
    sql = arguments.get("sql")
    if not sql:
        return [TextContent(type="text", text="Error: Missing 'sql' argument.")]

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        
        if name == "read_query":
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            # Format rows nicely into key-value pairs
            result_data = [dict(zip(columns, row)) for row in rows]
            output_text = str(result_data)
        else:
            conn.commit()
            output_text = f"Query executed successfully. Rows affected: {cursor.rowcount}"
            
        cursor.close()
        conn.close()
        return [TextContent(type="text", text=output_text)]
        
    except Exception as e:
        return [TextContent(type="text", text=f"Database Error: {str(e)}")]

# 3. Mount into FastAPI and Enforce Account Access Control
app = FastAPI()

# Track active transport streams mapped by client connection strings/IDs
sse_transport = SseServerTransport("/messages")

@app.middleware("http")
async def verify_account_access(request: Request, call_next):
    # Enforce authentication check on everything except root/health checks
    if request.url.path in ["/sse", "/messages"]:
        provided_key = request.headers.get("X-Account-Key") or request.query_params.get("key")
        if not provided_key or provided_key != MY_SECRET_ACCOUNT_KEY:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized: Invalid Account Key."})
            
    return await call_next(request)

# Correct official endpoints utilizing SseServerTransport state engines
@app.get("/")
async def root_fallback(request: Request):
    # This automatically routes root traffic directly to your MCP engine!
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as queue:
        await mcp_server.run(
            read_stream=queue,
            write_stream=sse_transport.handle_sse_channel,
            initialization_options=mcp_server.create_initialization_options()
        )

@app.post("/messages")
async def messages_endpoint(request: Request):
    return await sse_transport.handle_post_message(request.scope, request.receive, request._send)
