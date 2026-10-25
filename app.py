from irrigation import create_app
import argparse


def parse_args():
    parser = argparse.ArgumentParser(description="Run Flask app")
    parser.add_argument(
        "--port",
        type=int,
        default=7000,
        help="Port number (default: 7000)"
    )
    return parser.parse_args()

app = create_app()


if __name__ == '__main__':
    try:
        args = parse_args()
        port = args.port
        print(f"Starting Flask on port {port}")
        app.run(host='0.0.0.0', port=port, debug=True,use_reloader=False)
    except Exception as e:
        print(f"Error occurred while running the app: {e}")
    except KeyboardInterrupt:
        print("Application interrupted by user.")
        
