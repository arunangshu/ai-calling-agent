from flask import Flask, request, render_template, jsonify, send_file
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from dotenv import load_dotenv
import os
import io
import sqlite3
import pandas as pd
from pydub import AudioSegment
from pydub.playback import play
import tempfile
import base64
from deepgram import DeepgramClient, SpeakOptions
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Twilio Credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Google Generative AI Key
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Initialize Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Load Product Data into SQLite
DATABASE_FILE = 'Products.csv'
conn = sqlite3.connect(':memory:', check_same_thread=False)
df = pd.read_csv(DATABASE_FILE)
df.to_sql('inventory', conn, if_exists='replace', index=False)

# Deepgram TTS setup
deepgram = DeepgramClient()
deeptts = deepgram.speak.rest.v("1")

# Synthesize text to speech
def synthesize_audio(text, output_path):
    try:
        options = SpeakOptions(model="aura-asteria-en")
        json_input = {"text": text}
        deeptts.save(output_path, json_input, options)
        return True
    except Exception as e:
        app.logger.error(f"Error in TTS: {e}")
        return False

# GeminiPhoneAgent Class
class GeminiPhoneAgent:
    def __init__(self):
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        self.chat = None
        self.initialize_chat()

    def initialize_chat(self):
        global greeting 
        """Initialize the chat with the phone agent persona"""
        self.chat = self.model.start_chat(history=[])
        initial_prompt = """
        You are a vibrant salesman for a customer service department from K-G-P Mareketplace. Your task is to assist customers effectively while maintaining a professional, courteous, and concise tone throughout the conversation. Please adhere to the following guidelines:
        - Always write just the phone agent's response in plain english only with punctuation marks. Special characters are strictly not allowed.
        - Greet customers politely.
        - Listen attentively to their concerns and provide clear, solution-oriented responses.
        - You have the store's database connected so query them and don't give wrong answers.
        - You can use your general knowledge to answer relevant questions asked.
        - Ask clarifying questions only when necessary.
        - Avoid long explanations—keep responses brief.
        - You final target is to convince the user to buy something and close sales.
        - If the customer is satisfied and does not need anything else, conclude the call with a polite farewell and then type 'EXIT' to cut the call.
        - When asked, 'Who are you?', respond: 'I am a person who assists people with their queries.'
        - Understand the customer's sentiments and respond to them accordingly to make them feel valued.

        Ensure that the conversation ends with a professional goodbye before writing 'EXIT' after the customer confirms satisfaction.
        
        Database instructions:
        You are connected to the store's sql database named 'inventory'. If you need to retrieve any data, give a one line response: start your response as "SQL: " and send a sql code, and send only one command. DO NOT WRITE ANYTHING ELSE IN THE RESPONSE. You will get back the result as a reply starting with "SQL Response: ". Then you can either reply to the user or make a query again using "SQL: ".
        While searching for item, always search for close matches as exact match may not be always there, but that shouldn't discourage the user from buying. Be a salesman. You can make repeated SQL queries before replying to the user. For example, if you are not sure which category to search for, see the distinct categories of items available, then see the items in relevant categories.
        
        Database Summary:
        This database represents a inventory of products in a store. It contains columns 'Product Name', 'Category', 'Brand', 'Price in Rupees', 'Stock',' Description'
        
        First Few Lines of Database:
        Product Name,Category,Brand,Price in Rupees,Stock,Description
        Cotton T-Shirt,Clothing,Essentials,299,150,Comfortable cotton t-shirt available in various colors and sizes
        Denim Jeans,Clothing,Levis,1499,75,Classic fit denim jeans with straight leg design
        Running Shoes,Footwear,Nike,2999,45,Lightweight running shoes with cushioned sole
        Wheat Flour,Groceries,Aashirvaad,250,200,Premium quality wheat flour (5kg pack)
        
        You are calling the customer so start with a catchy line for him/her so that he/she wants to buy something. If you think you need to query inventory items then you can do that at the beginning (using "SQL: ") before talking to the user.
        Start with "Hello! This is Taaniya from KGP Market Place, your one-stop destination for amazing deals and top-quality products. We have some exciting offers tailored just for you—may I take a moment to share them?"
        """

        greeting = self.chat.send_message(initial_prompt).text
      

    def send_message(self, message):
        response = self.chat.send_message(message)
        return response.text

# Initialize Gemini Agent
phone_agent = GeminiPhoneAgent()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/make_call', methods=['POST'])
def make_call():
    try:
        phone_number = request.json.get('phone_number')
        if not phone_number:
            return jsonify({'status': 'error', 'message': 'Phone number is required.'}), 400
        call = twilio_client.calls.create(
            url=os.getenv('WEBHOOK_BASE_URL') + '/start_conversation',
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER
        )
        return jsonify({'status': 'success', 'call_sid': call.sid})
    except Exception as e:
        app.logger.error(f"Error making call: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    



@app.route('/start_conversation', methods=['POST'])
def start_conversation():
    global greeting
    response = VoiceResponse()
    #greeting = "Hello! This is Neha from KGP Market Place, your one-stop destination for amazing deals and top-quality products. We have some exciting offers tailored just for you—may I take a moment to share them"
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
        if synthesize_audio(greeting, temp_file.name):
            response.play(request.url_root + f'audio/{base64.b64encode(temp_file.name.encode()).decode()}')
        else:
            response.say(greeting)
    response.gather(input='speech', action='/process_conversation', timeout=3, language='en-US')
    return str(response)

@app.route('/process_conversation', methods=['POST'])
def process_conversation():
    response = VoiceResponse()
    call_sid = request.values.get('CallSid')
    user_speech = request.values.get('SpeechResult', '')

    # Handle user input with GeminiPhoneAgent
    bot_response = phone_agent.send_message(user_speech).strip()
    should_end = bot_response.endswith('EXIT')

    # Handle SQL queries in bot_response
    while bot_response.startswith("SQL:"):
        try:
            sql_query = bot_response[5:].strip()
            sql_result = pd.read_sql_query(sql_query, conn)
            response_message = f"SQL Response:\n{sql_result.to_string(index=False)}"
        except Exception as e:
            response_message = f"SQL Error: {e}"
        bot_response = phone_agent.send_message(response_message).strip()

    # If the conversation ends, remove 'EXIT'
    if should_end:
        bot_response = bot_response[:-4].strip()

    # Synthesize bot response
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
        if synthesize_audio(bot_response, temp_file.name):
            response.play(request.url_root + f'audio/{base64.b64encode(temp_file.name.encode()).decode()}')
        else:
            response.say(bot_response)

    # Gather further input unless exiting
    if not should_end:
        response.gather(input='speech', action='/process_conversation', timeout=3, language='en-US')

    return str(response)

@app.route('/audio/<filename>', methods=['GET'])
def serve_audio(filename):
    try:
        actual_filename = base64.b64decode(filename).decode()
        with open(actual_filename, 'rb') as audio_file:
            return send_file(io.BytesIO(audio_file.read()), mimetype='audio/mpeg')
    except Exception as e:
        app.logger.error(f"Error serving audio: {e}")
        return "Error serving audio", 500

if __name__ == '__main__':
    app.run(debug=True)
