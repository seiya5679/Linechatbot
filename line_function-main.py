# ================================
# 必要なライブラリのインポート
# ================================
import os
import boto3
import google.generativeai as genai
import pickle
import tempfile
import io
from PIL import Image
import json
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage, TextSendMessage,
    TemplateSendMessage, ButtonsTemplate, MessageAction
)

# ================================
# LINE Bot API設定
# ================================
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# ================================
# Google Gemini API設定
# ================================
genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))

# 画像解析用（Vision）とテキスト生成用（会話）
gemini_text = genai.GenerativeModel("gemini-2.0.-flash")   # 軽量高速モデル
gemini_vision = genai.GenerativeModel("gemini-2.0-flash") # 画像入力対応

# ================================
# AWS SDK設定
# ================================
rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('linebot')

# ================================
# DynamoDB関連関数
# ================================
def putItemToDynamoDB(id, val, chat):
    table.put_item(
        Item={
            "id": id,
            "val": val,
            "chat": chat,
        }
    )

def getItemFromDynamoDB(userID):
    try:
        response = table.get_item(Key={'id': userID})
        item = response.get('Item', None)
    except Exception:
        item = None
    return item


# ================================
# テキストメッセージ処理
# ================================
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event: MessageEvent):
    user_text = event.message.text.strip()
    user_id = event.source.user_id

    # --- 選択メニュー表示 ---
    if user_text.lower() in ["メニュー", "menu", "スタート", "start"]:
        message = TemplateSendMessage(
            alt_text='コーデ選択メニュー',
            template=ButtonsTemplate(
                title='AIコーデメニュー',
                text='どの方法でコーデを作りますか？',
                actions=[
                    MessageAction(label='👕 服の写真からコーデを作成', text='写真からコーデを作成'),
                    MessageAction(label='📝 テキストからコーデを生成', text='テキストからコーデを生成')
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # --- 写真モードの案内 ---
    if "写真から" in user_text:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📸 服の写真を送ってください！AIがコーデを提案します。")
        )
        return

    # --- テキストモード処理 ---
    if "テキストから" in user_text:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 どんなシーンのコーデを考えていますか？（例：デート・通学・オフィスなど）")
        )
        return

    # --- 通常のテキスト入力をコーデ生成として扱う ---
    prompt = f"次の要望に合うコーディネートを日本語で提案してください。自然な会話形式で。\n要望: {user_text}"
    response = gemini_text.generate_content(prompt)

    reply_text = response.text.strip() if response and response.text else "すみません、うまく提案できませんでした。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# ================================
# 画像メッセージ処理
# ================================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event: MessageEvent):
    user_id = event.source.user_id
    item = getItemFromDynamoDB(user_id)
 
    weather_info = "晴れ、気温25度"  #デフォルトの天気情報
    user_style = "指定なし"

    if item:
        if 'weather' in item:
            weather_info = item['weather']
        if 'style' in item:
            user_style = item['style']
    
    message_id = event.massage.id
    message_content = line_bot_api.get_massage_content(message_id)
    image_binary = message_content.content

    img = Image.open(io.BytesIO(image_binary))

    prompt = f"""
    あなたはプロのファッションスタイリストです。
    ユーザーから送られた写真の服をメインに使って、以下の条件に合うおしゃれなコーデを複数パターン提案してください。

    【条件】
    ・今日の天気: {weather_info}
    ・ユーザーの好み: {user_style}
    ・出力形式:タイトルと具体的なアイテムの組み合わせ、着こなしのポイントを簡潔に。

    提案の最後には、「このコーデに合うアイテムを探す」と一言添えてください。
    """

    try:
        response = gemini_model.generate_content([prompt, img])
        return_message = response.text

    except Exception as e:
        print(f"Gemini Error: {e}")
        return_message = "申し訳ありません。コーデの生成に失敗しました。"
 
    # LINEに返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=retrun_message)
    )
 

# ================================
# Lambdaエントリポイント
# ================================
def lambda_handler(event, context):
    try:
        body = json.loads(event["body"])
        signature = event["headers"]["x-line-signature"]
        handler.handle(body["events"][0], signature)
    except Exception as e:
        print("Error:", e)
    return {"statusCode": 200, "body": "OK"}
