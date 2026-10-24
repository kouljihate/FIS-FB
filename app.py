from irrigation import create_app

app = create_app()

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5001, debug=True)
    except Exception as e:
        print(f"Error occurred while running the app: {e}")
    except KeyboardInterrupt:
        print("Application interrupted by user.")
        
