from flask import Flask
import os

app = Flask('')

@app.route('/')
def home():
    return "DISCORD'S BLOODY RUNNIN"

def run():
    # Remove debug=True or set use_reloader=False to avoid the signal error
    app.run(host='0.0.0.0', port=8080, debug=False)

def keep_alive():
    from threading import Thread
    t = Thread(target=run)
    t.start()