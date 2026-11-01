"""Development entry point. Initialize explicitly with `flask --app app migrate`."""
from sobhe import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
