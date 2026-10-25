import argparse
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
from .config import ServerConfig
from .server import create_app


def main():
    parser=argparse.ArgumentParser(description='WebRTC 本地文件 MCP 服务端')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=9003)
    parser.add_argument('--env-file',type=Path,default=Path('file_mcp/.env'))
    args=parser.parse_args()
    load_dotenv(args.env_file)
    config=ServerConfig.from_env()
    uvicorn.run(create_app(config),host=args.host,port=args.port,workers=1)


if __name__=='__main__':main()
