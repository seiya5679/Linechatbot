# 必要なライブラリのインポート
import os                  # 環境変数を扱うため
import boto3               # AWS SDK (Rekognition, DynamoDB)
import google.generativeai as genai  # Google Gemini API
import pickle              # Pythonオブジェクトをバイナリ化して保存
import io
from PIL import Image      # 画像処理用
from linebot import LineBotApi, WebhookHandler  # LINE Messaging API用
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from linebot.models import TemplateSendMessage, ButtonsTemplate, FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent, MessageAction

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
        # Bubble の作成
        bubble = BubbleContainer(
            body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='どんな画像を生成しますか？'),
                    ButtonComponent(
                    action=MessageAction(label='ファッション', text='ファッション')
                ),
                ButtonComponent(
                    action=MessageAction(label='スポーツ', text='スポーツ')
                ),
                ButtonComponent(
                    action=MessageAction(label='音楽', text='音楽')
                ),
                ButtonComponent(
                    action=MessageAction(label='映画', text='映画')
                ),
            ]
            )
        )

# FlexSendMessage を作成
        flex_message = FlexSendMessage(
            alt_text='カテゴリ選択',
            contents=bubble
        )

# 返信
        line_bot_api.reply_message(event.reply_token, flex_message)
    else:
        reply = TextSendMessage(text="メニューから選択してください！")
        line_bot_api.reply_message(event.reply_token, reply)
# -------------------------------
# 画像メッセージ受信時の処理
# -------------------------------
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
    
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
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
        TextSendMessage(text=return_message)
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
