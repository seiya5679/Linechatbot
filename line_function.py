import os
import boto3
import google.generativeai as genai
import pickle
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))
genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('linebot')

def putItemToDynamoDB(id, val, chat):
    table.put_item(
        Item = {
            "id": id,
            "val" : val,
            "chat" : chat,
        }
    )

def getItemFromDynamoDB(userID):
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

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event: MessageEvent):
    userID = event.source.user_id
    item = getItemFromDynamoDB(userID)
    message = None
    if(item is None):
        message = "はじめまして！\n画像を投稿すると有名人を検出することができます！"
        chat = gemini_model.start_chat(history=[])
        print('debug:', type(chat))
        putItemToDynamoDB(userID, 0, pickle.dumps(chat.history))
    else:
        prompt = event.message.text
        history = pickle.loads(item['chat'].value)
        chat = gemini_model.start_chat(history=history)
        response = chat.send_message(prompt)
        #response = gemini_model.generate_content([prompt])
        message = response.text.rstrip('\n')
        putItemToDynamoDB(userID, 0, pickle.dumps(chat.history))
    line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=message))

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event: MessageEvent):
    userID = event.source.user_id
    item = getItemFromDynamoDB(userID)
    if(item is None):
        chat = gemini_model.start_chat(history=[])
        putItemToDynamoDB(userID, 0, pickle.dumps(chat.history))
        item = getItemFromDynamoDB(userID)
    putItemToDynamoDB(userID, item['val']+1, item['chat'])
    retrun_message = str(item['val']+1) + "回目の画像投稿です。\n"
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    message_binary = message_content.content
    detect = rekognition.detect_labels(
        Image={
            "Bytes": message_binary
        }
    )
    labels = detect['Labels']
    names = [label.get('Name') for label in labels]
    if ("Human" in names or "Person" in names):
        response = rekognition.recognize_celebrities(
            Image={
                "Bytes": message_binary
            }
        )
        if (len(response['CelebrityFaces']) > 0):
            for i in range(len(response['CelebrityFaces'])):
                retrun_message += response['CelebrityFaces'][i]['Name'] + '\n'
            retrun_message = retrun_message.rstrip('\n')
        else:
            retrun_message += "有名人を特定できませんでした！"
    else:
        retrun_message += "人物を検出できませんでした！"
    line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=retrun_message))

def lambda_handler(event, context):
    handler.handle(
        event['body'],
        event['headers']['x-line-signature'])
    return {'statusCode': 200, 'body': 'OK'}
