# 必要なライブラリのインポート
import os                  # 環境変数を扱うため
import boto3               # AWS SDK (Rekognition, DynamoDB)
import google.generativeai as genai  # Google Gemini API
import pickle              # Pythonオブジェクトをバイナリ化して保存
from linebot import LineBotApi, WebhookHandler  # LINE Messaging API用
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from linebot.models import TemplateSendMessage, ButtonsTemplate, FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent, MessageAction, QuickReply, QuickReplyButton, LocationMessage, LocationAction
from linebot.models import (
    BubbleContainer, BoxComponent, TextComponent, ImageComponent,
    ButtonComponent,CarouselContainer, FlexSendMessage, MessageAction
)
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

def putItemToDynamoDB(id,state, val, chat):
    """
    DynamoDBにデータを保存する関数
    - id: LINEユーザーID
    - val: 画像送信回数などのカウンター
    - chat: Geminiのチャット履歴をpickleで保存
    """
    table.put_item(
        Item = {
            "id": id,
            "state": state,
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
    user_message = event.message.text
    user_id = event.source.user_id
    # === ① DynamoDBからユーザーステートを取得 ===
    item = getItemFromDynamoDB(user_id)
    # === ② 初回ユーザーの場合の処理 ===
    if item is None:
        chat = gemini_model.start_chat(history=[])
        putItemToDynamoDB(
            id=user_id,
            state="INIT",                 # 初回状態
            val=0,
            chat=pickle.dumps(chat.history)
        )
        # 初回挨拶
        reply = TextSendMessage(text="こんにちは！このチャットボットでは当日のコーデ生成をテキスト形式、もしくは既に持っている服の写真から自動で生成します。画面下部のメニューから選択してください。")
        line_bot_api.reply_message(event.reply_token, reply)
        return
    # 現在のステート取得
    state = item["state"]

    # === ③ ステートごとの分岐 ===

    # ★ INIT → リッチメニューやテキストでモード選択
    if state == "INIT":
        if user_message == "画像から生成":
            putItemToDynamoDB(
                id=user_id,
                state="WAIT_IMAGE",           # 次は画像待ち状態
                chat=item["chat"],
                val=item["val"]
            )
            reply = TextSendMessage(text="持っている服の画像を送信してください！")
            line_bot_api.reply_message(event.reply_token, reply)
            return

        elif user_message == "テキストから生成":
            putItemToDynamoDB(
                id=user_id,
                state="ASK_AGE",
                chat=item["chat"],
                val=item["val"]
            )
            message = TextSendMessage(
                text="どんなカテゴリでコーデを組みますか？",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label=label, text=label))
                        for label in ["カジュアル系", "綺麗系", "フォーマル", "スポーツ","ストリート","その他"]
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        else:
            putItemToDynamoDB(
                id=user_id,
                state="INIT",# 初期状態に戻す
                chat=item["chat"],
                val=item["val"]
            )
            message = TextSendMessage(text="画面下部のメニューから選択してください")
            line_bot_api.reply_message(event.reply_token, message)
            return
    elif state == "ASK_AGE":
        putItemToDynamoDB(
            id=user_id,
            state="ASK_COLOR",
            chat=item["chat"],
            val=item["val"]
        )
        message = TextSendMessage(
                text="年齢を選んでください",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label=label, text=label))
                        for label in ["10代", "20代", "30代", "40代","50代","60代以上"]
                    ]
                )
            )
        line_bot_api.reply_message(event.reply_token, reply)
        return
    
    elif state == "ASK_COLOR":
        putItemToDynamoDB(
            id=user_id,
            state="ASK_PRICE",
            chat=item["chat"],
            val=item["val"]
        )
        message = TextSendMessage(
                text="どんな色でコーデを組みますか？",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label=label, text=label))
                        for label in ["明るめな色", "暗めな色", "派手目の色", "落ち着いた色","モノトーン","その他"]
                    ]
                )
            )
        line_bot_api.reply_message(event.reply_token, reply)
        return
    elif state == "ASK_PRICE":
        putItemToDynamoDB(
            id=user_id,
            state="ASK_LOCATION",
            chat=item["chat"],
            val=item["val"]
        )
        message = TextSendMessage(
                text="予算を選んでください",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label=label, text=label))
                        for label in ["1000円以下", "1000円〜5000円", "5000円〜10000円", "10000円以上","特に気にしない"]
                    ]
                )
            )
        line_bot_api.reply_message(event.reply_token, reply)
        return
    elif state == "ASK_LOCATION":
        putItemToDynamoDB(
            id=user_id,
            state="WAIT_LOCATION",
            chat=item["chat"],
            val=item["val"]
        )
        message = TextSendMessage(
            text="どこで服を着ていくか現在地を送ってください",
            quick_reply=QuickReply(
                items=[
                QuickReplyButton(action=LocationAction(label="位置情報を送信"))
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return
    elif state == "WAIT_LOCATION":
        putItemToDynamoDB(
            id=user_id,
            state="INIT",
            chat=item["chat"],
            val=item["val"]
        )
        # コーディネート生成処理をここに追加
        message = TextSendMessage(text="あなたにぴったりのコーディネートを生成しました！")
        line_bot_api.reply_message(event.reply_token, message)
        return

    
    
# -------------------------------
# 位置メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):
    user_id = event.source.user_id
    item = getItemFromDynamoDB(user_id)
    putItemToDynamoDB(
            id=user_id,
            state="INIT",
            chat=item["chat"],
            val=item["val"]
        )
    address = event.message.address # 住所
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"今までの会話とこの地域の天気や気温を考慮して画像を生成しました!")
    )




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
        putItemToDynamoDB(userID, 0, pickle.dumps(chat.history), state="初期状態")
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
