"""Run the portal behind a TLS reverse proxy on the control host."""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run("qcl_negf_api.api:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
