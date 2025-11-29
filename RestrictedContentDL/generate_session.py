from pyrogram import Client

api_id = 21691724
api_hash = "aaed0c61723d064fc51928efc54ba1df"

with Client("session_gen", api_id, api_hash) as app:
    print(app.export_session_string())
