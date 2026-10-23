import argparse
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
from .database import DatabaseConfig
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description='MySQL / PostgreSQL remote HTTP MCP')
    parser.add_argument('--engine', choices=['mysql','postgres'], required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--env-file', default=str(Path(__file__).with_name('.env')))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    try:
        config = DatabaseConfig.from_env(args.engine)
    except ValueError as exc:
        parser.error(str(exc))
    port = args.port or (9001 if args.engine == 'mysql' else 9002)
    uvicorn.run(create_app(config), host=args.host, port=port, log_level='info')


if __name__ == '__main__':
    main()
