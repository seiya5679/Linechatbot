import os
import json
import boto3
import google.generativeai as genai
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    TemplateSendMessage, ButtonsTemplate, MessageAction
)

# === LINE設定 ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === Gemini設定 ===
genai.configure(api_key=os.environ['GOOGLE_API_KEY'])

# === DynamoDB設定 ===
dynamodb = boto3.resource('dynamodb')
table_images = dynamodb.Table('UserImages')
table_selections = dynamodb.Table('UserSelections')

# === Lambda本体 ===
def lambda_handler(event, context):
    body = json.loads(event['body'])
    
    for ev in body['events']:
        user_id = ev['source']['userId']
        
        # 1️⃣ 画像受信
        if ev['type'] == 'message' and ev['message']['type'] == 'image':
            message_id = ev['message']['id']
            image_content = line_bot_api.get_message_content(message_id)
            image_bytes = image_content.content
            
            # DynamoDBに一時保存
            table_images.put_item(Item={
                'userId': user_id,
                'imageId': message_id,
                'imageData': image_bytes.hex(),
                'status': 'received'
            })
            
            # Geminiで解析
            analysis_result = analyze_image(image_bytes)
            table_images.update_item(
                Key={'userId': user_id, 'imageId': message_id},
                UpdateExpression="SET status=:s, analysisResult=:r",
                ExpressionAttributeValues={
                    ':s': 'analyzed',
                    ':r': analysis_result
                }
            )
            
            # ユーザーに確認
            send_clothing_confirmation(user_id, analysis_result['type'])
        
        # 2️⃣ 服タイプ確認・アイテム選択・価格選択の応答
        elif ev['type'] == 'message' and ev['message']['type'] == 'text':
            text = ev['message']['text']
            
            # 服タイプ確認
            if text.startswith('服タイプ確認:'):
                send_item_suggestions(user_id)
            
            # アイテム選択
            elif text.startswith('アイテム選択:'):
                selected_item = text.split(':')[1]
                table_selections.put_item(Item={
                    'userId': user_id,
                    'selectedItem': selected_item
                })
                ask_price_range(user_id)
            
            # 価格選択
            elif text.startswith('価格帯選択:'):
                price_range = text.split(':')[1]
                selected_item = table_selections.get_item(Key={'userId': user_id})['Item']['selectedItem']
                generate_final_recommendation(user_id, selected_item, price_range)
    
    return {'statusCode': 200}


# === Geminiで服解析 ===
def analyze_image(image_bytes):
    response = genai.generate_content(
        model="gemini-2.0-flash",
        prompt="Analyze this clothing item and return type, color, pattern in JSON format.",
        image=image_bytes
    )
    result = response.content  # JSON形式で返す想定
    return result


# === LINEで服確認 ===
def send_clothing_confirmation(user_id, clothing_type):
    buttons_template = ButtonsTemplate(
        title='服の確認',
        text=f"この服は{clothing_type}ですか？",
        actions=[
            MessageAction(label='はい', text='服タイプ確認:はい'),
            MessageAction(label='いいえ', text='服タイプ確認:いいえ')
        ]
    )
    line_bot_api.push_message(user_id, TemplateSendMessage(alt_text='服の確認', template=buttons_template))


# === 類似アイテム提案 ===
def send_item_suggestions(user_id):
    recommended_items = ["デニムパンツ", "カーゴパンツ", "スカート"]
    buttons_template = ButtonsTemplate(
        title="似合うアイテム",
        text="以下の中から選んでください",
        actions=[MessageAction(label=item, text=f"アイテム選択:{item}") for item in recommended_items]
    )
    line_bot_api.push_message(user_id, TemplateSendMessage(alt_text='似合うアイテム', template=buttons_template))


# === 価格帯選択 ===
def ask_price_range(user_id):
    price_options = [
        "1000~3000円",
        "3000~5000円",
        "5000~10000円",
        "10000円以上"
    ]
    buttons_template = ButtonsTemplate(
        title="価格帯を選択",
        text="希望の価格帯を選んでください",
        actions=[MessageAction(label=p, text=f"価格帯選択:{p}") for p in price_options]
    )
    line_bot_api.push_message(user_id, TemplateSendMessage(alt_text='価格帯選択', template=buttons_template))


# === 最終おすすめ生成 ===
def generate_final_recommendation(user_id, selected_item, price_range):
    prompt = f"ユーザーが選んだ服:{selected_item}, 価格帯:{price_range}. おすすめの服と購入サイトをJSONで返して。"
    response = genai.generate_content(model="gemini-2.0-flash", prompt=prompt)
    recommendation = response.content  # JSON形式想定
    
    line_bot_api.push_message(
        user_id,
        TextSendMessage(
            text=f"おすすめ: {recommendation['item_name']} ({recommendation['price']})\n購入サイト: {recommendation['site_url']}"
        )
    )
