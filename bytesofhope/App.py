import os

from web_app import app


def main():
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5001")),
    )


if __name__ == "__main__":
    main()
