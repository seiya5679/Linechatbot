# 必要なライブラリのインポート
import os
import boto3
import google.generativeai as genai
import pickle
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from linebot.models import TemplateSendMessage, ButtonsTemplate, MessageAction

# -------------------------------
# LINE Bot API の設定
# -------------------------------
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# -------------------------------
# Google Gemini API の設定
# -------------------------------
genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# -------------------------------
# AWS SDK 設定
# -------------------------------
rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('linebot')  # 既存テーブル

# -------------------------------
# DynamoDB 操作関数
# -------------------------------
def save_user_state(user_id, state):
    """ユーザーの会話ステートを保存"""
    table.update_item(
        Key={'id': user_id},
        UpdateExpression="set #s = :state",
        ExpressionAttributeNames={'#s': 'state'},
        ExpressionAttributeValues={':state': state}
    )

def get_user_state(user_id):
    """ステート取得"""
    data = getItemFromDynamoDB(user_id)
    if data and "state" in data:
        return data['state']
    return None

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
        return response.get('Item', None)
    except Exception as e:
        print("DynamoDB get_item error:", e)
        return None



# -------------------------------
# テキストメッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id

    # 現在のステートを取得
    state = get_user_state(user_id)

    # -------------------------------
    # ① リッチメニューの最初の選択
    # -------------------------------
    if user_message == "画像から生成":
        save_user_state(user_id, "WAITING_IMAGE")
        reply = TextSendMessage(text="画像を送ってください！")
        line_bot_api.reply_message(event.reply_token, reply)
        return

    if user_message == "テキストから生成":
        save_user_state(user_id, "WAITING_PROMPT")
        reply = TextSendMessage(text="どんな画像を生成しますか？")
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # -------------------------------
    # ② ステートに応じた次の会話
    # -------------------------------
    if state == "WAITING_PROMPT":
        # 次に送られたテキストを「画像生成用プロンプト」として扱う
        prompt = user_message

        reply = TextSendMessage(text=f"『{prompt}』の画像を生成します！ 少しお待ちください。")
        save_user_state(user_id, None)  # ステートリセット

        line_bot_api.reply_message(event.reply_token, reply)
        return

    # ステート未設定時のデフォルト応答
    reply = TextSendMessage(text="メニューから操作を選んでください！")
    line_bot_api.reply_message(event.reply_token, reply)


# -------------------------------
# 画像メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    state = get_user_state(user_id)

    # 画像待ちステートの場合のみ処理
    if state == "WAITING_IMAGE":
        reply = TextSendMessage(text="画像を受け取りました！解析しますね。")
        save_user_state(user_id, None)  # ステートリセット
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # ステートなしで画像送信された場合
    reply = TextSendMessage(text="まずメニューから『画像から生成』を選んでください！")
    line_bot_api.reply_message(event.reply_token, reply)
