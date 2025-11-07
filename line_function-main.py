import os
import boto3
import json
import tempfile
import requests
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage,
    TextSendMessage
)
import google.generativeai as genai

# ====== 環境変数から設定 ======
line_bot_api = LineBotApi(os.environ.get("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.environ.get("CHANNEL_SECRET"))
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

# ====== Geminiモデルの初期化 ======
gemini_text = genai.GenerativeModel("gemini-2.0-flash")
gemini_vision = genai.GenerativeModel("gemini-2.0-flash")

# ====== Lambdaのメイン処理 ======
def lambda_handler(event, context):
    body = json.loads(event["body"])
    signature = event["headers"]["x-line-signature"]

    try:
        handler.handle(body["events"][0], signature)
    except Exception as e:
        print("Error:", e)

    return {"statusCode": 200, "body": "OK"}


# ====== テキストメッセージ処理 ======
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text

    if "写真から" in user_text:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 服の写真を送ってください！その服に合うコーデを提案します。")
        )
        return

    elif "テキストから" in user_text:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 どんなコーデを考えていますか？（例：デート・通学・お出かけなど）")
        )
        return

    else:
        # Gemini Text呼び出し
        prompt = f"ユーザーの要望『{user_text}』に合うファッションコーデを自然な会話形式で提案してください。"
        response = gemini_text.generate_content(prompt)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response.text.strip())
        )


# ====== 画像メッセージ処理 ======
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # 画像を一時保存
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        for chunk in message_content.iter_content():
            tmp.write(chunk)
        tmp_path = tmp.name

    # Gemini Visionで解析
    with open(tmp_path, "rb") as img_file:
        response = gemini_vision.generate_content([
            "この服に合うコーデを提案してください。",
            {"mime_type": "image/jpeg", "data": img_file.read()}
        ])

    # 結果を返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response.text.strip())
    )
