# -*- coding: utf-8 -*-
import os
import boto3
import google.generativeai as genai
import pickle
from botocore.exceptions import ClientError
import requests
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    TemplateSendMessage, ButtonsTemplate, FlexSendMessage,
    BubbleContainer, BoxComponent, TextComponent, ButtonComponent,
    MessageAction, QuickReply, QuickReplyButton,
    LocationMessage, LocationAction
)

# -------------------------------
# 設定
# -------------------------------
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

s3 = boto3.client('s3')
S3_BUCKET = os.environ['S3_BUCKET']

rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('linebot')   # ←必要ならテーブル名を変更してください

# -------------------------------
# DynamoDBユーティリティ
# -------------------------------
def save_session(user_id: str, key: str, value):
    """
    会話で選んだ項目を保存（上書き更新）。
    - key は DynamoDB の属性名（任意の文字列）を想定
    - value は文字列/数値/リストなど（boto3が自動でDynamoDB形式に変換）
    安全のため ExpressionAttributeNames を使って予約語を避ける。
    """
    try:
        table.update_item(
            Key={"id": user_id},
            UpdateExpression="SET #k = :v",
            ExpressionAttributeNames={"#k": key},
            ExpressionAttributeValues={":v": value},
            ReturnValues="NONE"
        )
    except ClientError as e:
        # 実運用ではログ出力（CloudWatch）する
        print(f"save_session error: {e}")
        raise

def get_session(user_id: str) -> dict:
    """
    保存されたユーザーの会話内容をすべて取得。
    - ユーザーが存在しない場合は空辞書を返す
    """
    try:
        resp = table.get_item(Key={"id": user_id})
        return resp.get("Item", {}) or {}
    except ClientError as e:
        print(f"get_session error: {e}")
        return {}

# -------------------------------
# テキストメッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    # LINE SDK のイベントオブジェクトはバージョンによって user id の取り方が異なる場合があるので
    # 該当環境で event.source.user_id が正しいことを確認してください。
    user_id = event.source.user_id

    # -------------------------
    # 画像から生成
    # -------------------------
    if user_message == "画像から生成":
        reply = TextSendMessage(text="画像を送信してください！")
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # -------------------------
    # テキストから生成スタート
    # -------------------------
    elif user_message == "テキストから生成":
        message = TextSendMessage(
            text="どんなカテゴリーでコーデを組みますか？",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["カジュアル系", "綺麗系", "フォーマル", "スポーツ", "ストリート"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # -------------------------
    # カテゴリー選択
    # -------------------------
    elif user_message in ["カジュアル系", "綺麗系", "フォーマル", "スポーツ", "ストリート"]:
        # 保存
        save_session(user_id, "category", user_message)

        message = TextSendMessage(
            text="年齢を選んでください",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["10代", "20代", "30代", "40代", "50代", "60代以上"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # -------------------------
    # 年齢選択
    # -------------------------
    elif user_message in ["10代", "20代", "30代", "40代", "50代", "60代以上"]:
        save_session(user_id, "age", user_message)

        message = TextSendMessage(
            text="どんな色でコーデを組みますか？",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["明るめな色", "暗めな色", "派手目の色", "落ち着いた色", "モノトーン"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # -------------------------
    # 色選択
    # -------------------------
    elif user_message in ["明るめな色", "暗めな色", "派手目の色", "落ち着いた色", "モノトーン"]:
        save_session(user_id, "color", user_message)

        message = TextSendMessage(
            text="予算を選んでください",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in [
                        "1000円以下",
                        "1000円〜5000円",
                        "5000円〜10000円",
                        "10000円以上",
                        "特に気にしない"
                    ]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # -------------------------
    # 予算選択
    # -------------------------
    elif user_message in ["1000円以下", "1000円〜5000円", "5000円〜10000円", "10000円以上", "特に気にしない"]:
        save_session(user_id, "budget", user_message)

        message = TextSendMessage(
            text="どこで服を着ていくか現在地を送ってください",
            quick_reply=QuickReply(
                items=[
                    # QuickReply の位置情報送信ボタン
                    QuickReplyButton(action=LocationAction(label="位置情報を送信"))
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    # -------------------------
    # 履歴確認（便利コマンド）
    # -------------------------
    elif user_message in ["履歴", "会話履歴", "ログ"]:
        session = get_session(user_id)
        if not session:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="まだ保存されたデータがありません。"))
            return

        result_text = (
            "保存済みの入力内容:\n"
            f"・カテゴリー: {session.get('category', '未選択')}\n"
            f"・年齢: {session.get('age', '未選択')}\n"
            f"・色: {session.get('color', '未選択')}\n"
            f"・予算: {session.get('budget', '未選択')}\n"
            f"・住所: {session.get('address', '未送信')}\n"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result_text))
        return

    # -------------------------
    # どれにも当てはまらない入力
    # -------------------------
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="すみません、その入力は処理できません。メニューから選び直すか「テキストから生成」を押してください。")
        )
        return


# -------------------------------
# 位置メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):
    user_id = event.source.user_id

    address = event.message.address

    # 保存（数値・文字列どちらでもOK）
    save_session(user_id, "address", address)

    # 会話で保存された内容を取得
    session = get_session(user_id)

    result_text = (
        "今までの会話から最適なコーデを生成しました！\n\n"
        f"■ カテゴリー: {session.get('category', '未選択')}\n"
        f"■ 年齢: {session.get('age', '未選択')}\n"
        f"■ 色: {session.get('color', '未選択')}\n"
        f"■ 予算: {session.get('budget', '未選択')}\n"
        f"■ 場所: {session.get('address', '未送信')}\n"
    )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=result_text)
    )

# -------------------------------
# 画像メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event: MessageEvent):
    user_id = event.source.user_id

    # ---- LINE から画像データ取得 ----
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    image_bytes = message_content.content

    # ---- S3 にアップロード ----
    s3_key = f"users/{user_id}/{message_id}.jpg"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=image_bytes,
        ContentType="image/jpeg"
    )

    # ---- Rekognition でラベル検出 ----
    rekog_res = rekognition.detect_labels(
        Image={"Bytes": image_bytes},
        MaxLabels=10,
        MinConfidence=70
    )
    labels = [label["Name"] for label in rekog_res["Labels"]]

    # ---- Rekognition で人物検出 ----
    has_person = "Person" in labels or "Human" in labels

    # ---- Gemini Vision でコーデ提案 ----
    prompt = f"""
あなたはプロのスタイリストです。

これは服のラベルです: {labels}

この服を使ったおしゃれなコーデを3つ提案して
"""

    # Gemini Vision 解析
    gemini_res = gemini_model.generate_content(
        [prompt, {"mime_type": "image/jpeg", "data": image_bytes}]
    )

    reply_text = gemini_res.text

    # ---- LINE に返信 ----
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
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
