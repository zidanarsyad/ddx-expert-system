"""python -m ddxes.web [--host 127.0.0.1] [--port 8000]"""
import argparse


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m ddxes.web", description="Run the DDX-ES web interface.")
    p.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    p.add_argument("--reload", action="store_true", help="restart when the source changes (development)")
    a = p.parse_args(argv)
    import uvicorn
    print(f"DDX-ES web interface: http://{a.host}:{a.port}  (Ctrl+C to stop)")
    uvicorn.run("ddxes.web.app:app", host=a.host, port=a.port, reload=a.reload, log_level="warning")


if __name__ == "__main__":
    main()
