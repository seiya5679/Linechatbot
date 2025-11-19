# 必要なライブラリのインポート
import os                  # 環境変数を扱うため
import boto3               # AWS SDK (Rekognition, DynamoDB)
import google.generativeai as genai  # Google Gemini API
import pickle              # Pythonオブジェクトをバイナリ化して保存
from linebot import LineBotApi, WebhookHandler  # LINE Messaging API用
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from linebot.models import TemplateSendMessage, ButtonsTemplate, MessageAction

# -------------------------------
# LINE Bot API の設定
# -------------------------------
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))  # LINEチャネルのアクセストークン
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))         # LINEチャネルシークレット

# -------------------------------
# Google Gemini API の設定
# -------------------------------
genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))  # 環境変数からAPIキーを取得
gemini_model = genai.GenerativeModel("gemini-2.0-flash")   # 使用するGeminiモデルを指定

# -------------------------------
# AWS SDK 設定
# -------------------------------
rekognition = boto3.client('rekognition')  # 画像解析用クライアント
dynamodb = boto3.resource('dynamodb')     # DynamoDBリソース取得
table = dynamodb.Table('linebot')         # DynamoDBのテーブル名

# -------------------------------
# DynamoDB操作関数
# -------------------------------

def putItemToDynamoDB(id, val, chat):
    """
    DynamoDBにデータを保存する関数
    - id: LINEユーザーID
    - val: 画像送信回数などのカウンター
    - chat: Geminiのチャット履歴をpickleで保存
    """
    table.put_item(
        Item = {
            "id": id,
            "val" : val,
            "chat" : chat,
        }
    )

def getItemFromDynamoDB(userID):
    """
    DynamoDBからデータを取得する関数
    - userID: LINEユーザーID
    - データが存在しない場合はNoneを返す
    """
    try:
        response = table.get_item(
            Key={
                'id': userID,
            }
        )
        item = response['Item']
    except Exception as e:
        item = None
    return item

# -------------------------------
# テキストメッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text  # ← リッチメニューで設定した文字列
    user_id = event.source.user_id

    if user_message == "画像から生成":
        reply = TextSendMessage(text="画像を送信してください！")
        line_bot_api.reply_message(event.reply_token, reply)
    elif user_message == "テキストから生成":
        reply = [
            TextSendMessage(text="テキストを入力してください。"),
            TemplateSendMessage(
            alt_text='ボタンテンプレート',
            template=ButtonsTemplate(
                title='カテゴリ選択',
                text='どんな画像を生成しますか？',
                actions=[
                    MessageAction(label='ファッション', text='ファッション'),
                    MessageAction(label='スポーツ', text='スポーツ'),
                    MessageAction(label='音楽', text='音楽'),
                    MessageAction(label='映画', text='映画'),
                ]
                )
            )
        ]
        line_bot_api.reply_message(event.reply_token, reply)
    else:
        reply = TextSendMessage(text="メニューから選択してください！")
        line_bot_api.reply_message(event.reply_token, reply)
# -------------------------------
# 画像メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event: MessageEvent):
    userID = event.source.user_id
    item = getItemFromDynamoDB(userID)

    # 初回ユーザーの場合の処理
    if item is None:
        chat = gemini_model.start_chat(history=[])
        putItemToDynamoDB(userID, 0, pickle.dumps(chat.history))
        item = getItemFromDynamoDB(userID)

    # 画像送信回数を更新
    putItemToDynamoDB(userID, item['val']+1, item['chat'])
    retrun_message = str(item['val']+1) + "回目の画像投稿です。\n"

    # 画像データ取得
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    message_binary = message_content.content  # バイナリデータとして取得

    # Rekognitionでラベル検出
    detect = rekognition.detect_labels(
        Image={
            "Bytes": message_binary
        }
    )
    labels = detect['Labels']
    names = [label.get('Name') for label in labels]

    # 人物が含まれるか確認
    if "Human" in names or "Person" in names:
        response = rekognition.recognize_celebrities(
            Image={
                "Bytes": message_binary
            }
        )
        if len(response['CelebrityFaces']) > 0:
            # 有名人を検出できた場合
            for celeb in response['CelebrityFaces']:
                retrun_message += celeb['Name'] + '\n'
            retrun_message = retrun_message.rstrip('\n')
        else:
            retrun_message += "有名人を特定できませんでした！"
    else:
        retrun_message += "人物を検出できませんでした！"

    # LINEに返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=retrun_message)
    )

# -------------------------------
# Lambda関数のエントリポイント
# -------------------------------
def lambda_handler(event, context):
    """
    AWS Lambda用エントリポイント
    LINEのWebhookイベントを処理
    """
    handler.handle(
        event['body'],
        event['headers']['x-line-signature']
    )
    return {'statusCode': 200, 'body': 'OK'}
