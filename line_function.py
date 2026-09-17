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
    LocationMessage, LocationAction, ImageSendMessage
)

import uuid  # ファイル名生成用に追加

import re
import json
import urllib.parse

# ======================
# Amazon検索リンク生成
# ======================
def amazon_search(keyword: str) -> str:
    q = urllib.parse.quote(keyword)
    return f"https://www.amazon.co.jp/s?k={q}"

# ======================
# 検索キーワード生成
# ======================
def build_keywords(session: dict):
    gender = "メンズ" if session.get("gender") != "女性" else "レディース"
    color = session.get("color", "白").replace("な色", "")
    category = session.get("category", "カジュアル").replace("系", "")

    return {
        "tops": f"{color} オーバーサイズ シャツ {gender}",
        "bottoms": f"{color} スラックス テーパード {gender}",
        "shoes": f"黒 レザー ローファー {gender}"
    }


# -------------------------------
# 設定
# -------------------------------
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

imagen_model = genai.GenerativeModel("imagen-3.0-generate-001")

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


# ======================
# 新規追加：画像生成とS3保存
# ======================

def generate_and_upload_image(fashion_description: str):
    """
    Gemini(Imagen)で画像を生成し、S3にアップロードして署名付きURLを返す
    """
    try:
        # 1. 画像生成
        prompt = f"A high-quality fashion photography of a person wearing: {fashion_description}. Highly detailed, professional lighting, lookbook style."
        response = imagen_model.generate_content(prompt)
        
        # モデルの戻り値から画像バイトを取得（SDKの仕様により異なる場合があります）
        image_bytes = response.candidates[0].content.parts[0].inline_data.data
        
        # 2. S3へアップロード
        file_name = f"osharebot-1234/{uuid.uuid4()}.jpg"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=file_name,
            Body=image_bytes,
            ContentType='image/jpeg'
        )
        
        # 3. LINE送信用の署名付きURLを発行 (有効期限1時間)
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': file_name},
            ExpiresIn=3600
        )
        return url
    except Exception as e:
        print(f"Image generation/S3 error: {e}")
        return None


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
            text="どちらの性別のコーデを希望しますか？",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["男性", "女性"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    elif user_message in ["男性"]:
        save_session(user_id, "gender", user_message)
        message = TextSendMessage(
            text="どんなカテゴリーでコーデを組みますか？",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["カジュアル系", "アメカジ","綺麗系", "フォーマル", "スポーツ","ビンテージ", "デザイナーズ","ストリート","地雷系"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    elif user_message in ["女性"]:
        save_session(user_id, "gender", user_message)
        message = TextSendMessage(
            text="どんなカテゴリーでコーデを組みますか？",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["カジュアル系","綺麗系", "フォーマル", "スポーツ","エレガンス","ガーリー","デザイナーズ","ストリート","地雷系"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return
    # -------------------------
    # カテゴリー選択
    # -------------------------
    elif user_message in ["カジュアル系", "綺麗系", "フォーマル", "スポーツ", "ストリート","エレガンス","ガーリー","アメカジ","ビンテージ","デザイナーズ","地雷系"]:
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

    elif user_message in ["明るめな色", "暗めな色", "派手目の色", "落ち着いた色", "モノトーン"]:
        save_session(user_id, "color", user_message)

        message = TextSendMessage(
            text="季節を選んでください",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in ["春", "夏", "秋", "冬"]
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return


    # -------------------------
    # 色選択
    # -------------------------
    elif user_message in ["春", "夏", "秋", "冬"]:
        save_session(user_id, "season", user_message)

        message = TextSendMessage(
            text="コーデの一式の予算を選んでください",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label=label, text=label))
                    for label in [
                        "10000円以内",
                        "10000円〜20000円",
                        "20000円〜30000円",
                        "30000円以上",
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
    elif user_message in ["10000円以内", "10000円〜20000円", "20000円〜30000円", "30000円以上", "特に気にしない"]:
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
            f"・性別: {session.get('gender', '未選択')}\n"
            f"・カテゴリー: {session.get('category', '未選択')}\n"
            f"・年齢: {session.get('age', '未選択')}\n"
            f"・色: {session.get('color', '未選択')}\n"
            f"・季節: {session.get('season', '未選択')}\n"
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
    save_session(user_id, "address", address)
    session = get_session(user_id)

    # Gemini
    prompt = f"""
以下の条件から、実用的で真似しやすいコーデを1つ提案してください。

【条件】
- 性別: {session.get('gender', 'メンズ')}
- 年齢: {session.get('age', '20代')}
- 系統: {session.get('category', 'カジュアル')}
- 色: {session.get('color', '白')}
- 季節: {session.get('season', '春')}
- 予算: {session.get('budget', '普通')}
- 行く場所: {address}

【要件】
- トップス・ボトムス・靴を具体的に
- コーデを決めた理由も簡単に説明
- 実用的でシンプル
- 最後に「JSONのみ」を出力する
- JSONの前後に説明文や ``` は付けない

【出力ルール】
・通常の文章
・最後の行にJSONだけを書く

【JSON例】
{{
  "tops": "白シャツ メンズ",
  "bottoms": "黒 スラックス メンズ",
  "shoes": "ローファー メンズ"
}}
"""

    gemini_res = gemini_model.generate_content(prompt)

    raw_text = gemini_res.text

    # -------------------------
    # Gemini出力を分解
    # -------------------------
    json_match = re.search(r'\{[\s\S]*\}', raw_text)
    keywords = json.loads(json_match.group()) if json_match else {}

    display_text = (
        raw_text.replace(json_match.group(), "").strip()
        if json_match else raw_text
    )

    # --- 画像生成処理の追加 ---
    fashion_summary = f"{keywords.get('tops')}, {keywords.get('bottoms')}, {keywords.get('shoes')}"
    image_url = generate_and_upload_image(fashion_summary)

    messages = []

    if image_url:
        messages.append(ImageSendMessage(
            original_content_url=image_url,
            preview_image_url=image_url
        ))

    # -------------------------
    # Flex Message（画像なし）
    # -------------------------
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "コーデ提案",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": display_text,
                    "wrap": True,
                    "size": "sm"
                },
                {"type": "separator"},
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 トップスを見る",
                        "uri": amazon_search(
                            keywords.get("tops", "メンズ トップス")
                        )
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 ボトムスを見る",
                        "uri": amazon_search(
                            keywords.get("bottoms", "メンズ パンツ")
                        )
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 靴を見る",
                        "uri": amazon_search(
                            keywords.get("shoes", "メンズ シューズ")
                        )
                    }
                }
            ]
        }
    }

    messages.append(FlexSendMessage(alt_text="コーデ提案", contents=flex_content))

    # -------------------------
    # LINE返信（※1回だけ）
    # -------------------------
    line_bot_api.reply_message(
        event.reply_token,
        FlexSendMessage(
            alt_text="画像からおすすめコーデ（Amazon）",
            contents=flex_content
        )
    )
# -------------------------------
# 画像メッセージ受信時の処理
# -------------------------------
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event: MessageEvent):
    user_id = event.source.user_id
    session = get_session(user_id)

    # -------------------------
    # LINEから画像取得
    # -------------------------
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    image_bytes = message_content.content

    # -------------------------
    # Rekognitionでラベル検出
    # -------------------------
    rekog_res = rekognition.detect_labels(
        Image={"Bytes": image_bytes},
        MaxLabels=5,
        MinConfidence=70
    )
    labels = [label["Name"] for label in rekog_res["Labels"]]

    # -------------------------
    # Gemini Vision（解析のみ）
    # -------------------------
    prompt = f"""
以下の画像解析結果から、
その服に似合うコーデを1つ提案してください。

【画像ラベル】
{labels}

【要件】
- トップス・ボトムス・靴を具体的に
- コーデを決めた理由も簡単に説明
- 実用的でシンプル
- 最後に「JSONのみ」を出力する
- JSONの前後に説明文や ``` は付けない

【出力ルール】
・通常の文章
・最後の行にJSONだけを書く

【JSON例】
{{
  "tops": "白シャツ メンズ",
  "bottoms": "黒 スラックス メンズ",
  "shoes": "ローファー メンズ"
}}
"""

    gemini_res = gemini_model.generate_content(
        [prompt, {"mime_type": "image/jpeg", "data": image_bytes}]
    )

    raw_text = gemini_res.text

    # -------------------------
    # Gemini出力を分解
    # -------------------------
    json_match = re.search(r'\{[\s\S]*\}', raw_text)
    keywords = json.loads(json_match.group()) if json_match else {}

    display_text = (
        raw_text.replace(json_match.group(), "").strip()
        if json_match else raw_text
    )

    fashion_summary = f"{keywords.get('tops')}, {keywords.get('bottoms')}, {keywords.get('shoes')}"
    image_url = generate_and_upload_image(fashion_summary)

    messages = []
    if image_url:
        messages.append(ImageSendMessage(original_content_url=image_url, preview_image_url=image_url))
    
    # -------------------------
    # Flex Message（画像なし）
    # -------------------------
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "📸 画像からのコーデ提案",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": display_text,
                    "wrap": True,
                    "size": "sm"
                },
                {"type": "separator"},
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 トップスを見る",
                        "uri": amazon_search(
                            keywords.get("tops", "メンズ トップス")
                        )
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 ボトムスを見る",
                        "uri": amazon_search(
                            keywords.get("bottoms", "メンズ パンツ")
                        )
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "🛒 靴を見る",
                        "uri": amazon_search(
                            keywords.get("shoes", "メンズ シューズ")
                        )
                    }
                }
            ]
        }
    }
    messages.append(FlexSendMessage(alt_text="コーデ提案", contents=flex_content))
    # -------------------------
    # LINE返信（※1回だけ）
    # -------------------------
    line_bot_api.reply_message(
        event.reply_token,
        FlexSendMessage(
            alt_text="画像からおすすめコーデ（Amazon）",
            contents=flex_content,
            messages=messages
        )
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
