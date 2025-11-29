# Copyright (C) @TheSmartBisnu
# Channel: https://t.me/itsSmartDev

from os import getenv
from time import time
from dotenv import load_dotenv

try:
    load_dotenv("config.env")
except:
    pass

    if not getenv("BOT_TOKEN") or not getenv("BOT_TOKEN").count(":") == 1:
        print("Error: BOT_TOKEN must be in format '123456:abcdefghijklmnopqrstuvwxyz'")
        exit(1)

    if (
        not getenv("SESSION_STRING")
        or getenv("SESSION_STRING") == "xxxxxxxxxxxxxxxxxxxxxxx"
    ):
        print("Error: SESSION_STRING must be set with a valid string")
        exit(1)


# Pyrogram setup
class PyroConf(object):
    API_ID = 21691724
    OPENAI_API_KEY="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"
    API_HASH = "aaed0c61723d064fc51928efc54ba1df"
    BOT_TOKEN = "8461849017:AAHh34vWEuoUp9JjFoF9bbe5cId4wCPLkTo"  # if using bot
    SESSION_STRING = "BQFK_UwAw9S7qBR5ZauCDVAAW2HWjzROpUqRTx4WOwpDDMvg51KGWCRRqmJjfMwHHzNswtx_Pmk6agGBXYaoGqlR3mv4mBFhjiIUrshrwXADTwu9-vQcX0T8sHqyXMyNegCt4Mla2d0FAEm4hAaU-ZFV4WlGni7TxsrWdX6x8oYA6LPNetTKKdSob_JO97ohbHLMDxhYpceus-DxXfuvmwaLrdjEopjREolZy39grz1vdX_51XIZeAKtevil8NRGFsS6_5ODTcMeoFR7bzBaX-NM0GfK6zF2sUn7NOdNyLFAtfKzDUGTmQO-Vj8SJvyQFzyIecGrNKA7eG7dp2zDgCej0eTBlQAAAABrQhXIAA"
    BOT_START_TIME = time()