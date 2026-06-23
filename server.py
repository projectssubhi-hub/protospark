import os
import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport

# 1. Initialize the Core MCP Server
mcp_server = Server("secure-free-db-server")

DATABASE_URL = os.environ.get("postgresql://neondb_owner:npg_xQ62JreXnyEz@ep-falling-violet-at7kfokg-pooler.c-9.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
MY_SECRET_ACCOUNT_KEY = os.environ.get("000000")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# 2. Register Database Tools
@mcp_server.list_tools()
async def handle_list_tools():
    return [
        Tool(
            name="read_query",
            description="Run SELECT queries on the database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The SELECT SQL statement."}
                },
                "required": ["sql"]
            }
        ),
        Tool(
            name="write_and_edit_query",
            description="Run INSERT, UPDATE, DELETE, or schema modifications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The mutation SQL statement."}
                },
                "required": ["sql"]
            }
        )
    ]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
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

# 3. FastAPI App & Official SSE Setup
app = FastAPI()
sse = SseServerTransport("/messages")

# Security Middleware for your account key
@app.middleware("http")
async def verify_account_access(request: Request, call_next):
    if request.url.path in ["/sse", "/messages"]:
        provided_key = request.headers.get("X-Account-Key") or request.query_params.get("key")
        if not provided_key or provided_key != MY_SECRET_ACCOUNT_KEY:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized: Invalid Account Key."})
    return await call_next(request)

# Correctly handle SSE connection stream via SDK context manager
@app.get("/sse")
async def sse_endpoint(request: Request):
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )

# Route to process post-handshake messages
@app.post("/messages")
async def messages_endpoint(request: Request):
    return await sse.handle_post_message(request.scope, request.receive, request._send)
