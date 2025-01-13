from dotenv import load_dotenv
from time import sleep
import logging
import pyaudio  # For cross-platform audio capture
import google.generativeai as genai
import os
from datetime import datetime
import json
from pathlib import Path
import subprocess
from deepgram.utils import verboselogs
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    SpeakOptions,
)
from pydub import AudioSegment
from pydub.playback import play
import sqlite3
import pandas as pd

# Load environment variables
load_dotenv()

def synthesize_audio_from_text(text_input, output_file):
    """Converts text to speech and saves it to an audio file."""
    try:
        global deeptts
    
        options = SpeakOptions(
            model="aura-asteria-en",
        )
        json_input={"text":text_input}
        # print(json_input)
        response = deeptts.save(output_file, json_input, options)
        # print(response)
        
        #Play the file
        play(AudioSegment.from_mp3(output_file))

    except Exception as e:
        print(f"Exception: {e}")

class GeminiPhoneAgent:
    def __init__(self, api_key):
        self.api_key = api_key
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        self.chat = None
        self.initialize_chat()

    def initialize_chat(self):
        """Initialize the chat with the phone agent persona"""
        self.chat = self.model.start_chat(history=[])
        initial_prompt = """
        You are a professional phone agent for a customer service department. Your task is to assist customers effectively while maintaining a professional, courteous, and concise tone throughout the conversation. Please adhere to the following guidelines:
        - Always write just the phone agent's response in plain english only with punctuation marks. Special characters are strictly not allowed.
        - Greet customers politely.
        - Listen attentively to their concerns and provide clear, solution-oriented responses.
        - You have the store's database connected so query them and don't give wrong answers.
        - You can use your general knowledge to answer relevant questions asked.
        - Ask clarifying questions only when necessary.
        - Avoid long explanations—keep responses brief.
        - If the customer is satisfied and does not need anything else, conclude the call with a polite farewell and then type 'EXIT' to cut the call.
        - When asked, 'Who are you?', respond: 'I am a person who assists people with their queries.'

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
        """
        self.chat.send_message(initial_prompt)

    def send_message(self, message):
        """Send a message to the phone agent and get response"""
        try:
            response = self.chat.send_message(message)
            return response.text
        except Exception as e:
            return f"Error: {str(e)}"

def setup_agent():
    """Setup the phone agent with API key"""
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        api_key = input("Please enter your Google API key: ")
    return GeminiPhoneAgent(api_key)

# Initialize Deepgram Client
is_finals = []
agent = setup_agent()

def main():
    try:
        global should_exit, deeptts, conn
        should_exit=False
        
        # Load CSV into a pandas DataFrame
        print('Loading database...')
        df = pd.read_csv('Products.csv') 

        # Connect to an in-memory SQLite database
        conn = sqlite3.connect(':memory:', check_same_thread=False)

        # Load the DataFrame into the SQLite database
        df.to_sql('inventory', conn, if_exists='replace', index=False)
        print('Database loaded!\n')
    
        # Initialize Deepgram client with default configuration
        print('Connecting to Deepgram API...')
        deepgram = DeepgramClient()
        deeptts=deepgram.speak.rest.v("1")
        dg_connection = deepgram.listen.websocket.v("1")

        # Event handlers
        def on_open(self, open, **kwargs):
            print("Connection Open")

        def on_message(self, result, **kwargs):
            global is_finals, should_exit, conn
            sentence = result.channel.alternatives[0].transcript
            if len(sentence) == 0:
                return
            if result.is_final:
                is_finals.append(sentence)
                if result.speech_final:

                    
                    utterance = " ".join(is_finals)
                    print(f"Final Speech: {utterance}")

                    # Pass the final transcription to the Gemini API
                    response = agent.send_message(utterance).strip()
                    print(f"Gemini Agent: {response}")

                    # Query handling
                    while response.startswith("SQL:"):
                        # Query the database using SQL
                        sql_response=''
                        try:
                            sql_response = 'SQL Response:\n' + str(pd.read_sql_query(response[5:].splitlines()[0], conn))
                        except Exception as e:
                            sql_response+='\n'+f"Error: {e}"
                        print(sql_response)
                        response = agent.send_message(sql_response).strip()
                        # Displaying the reply
                        print(f"Gemini Agent: {response}")

                    # Remove EXIT if present in the response
                    if response[-4:]=='EXIT':
                        response = response[:-6]
                        should_exit=True
                    elif response[-8:]=='**EXIT**':
                        response = response[:-10]
                        should_exit=True

                    # Convert Gemini's response to audio
                    output_file = "output_audio.mp3"
                    synthesize_audio_from_text(response, output_file)
                    
                    is_finals = []

                    # Exit if EXIT command was detected
                    if should_exit:
                        print("Detected EXIT command...")
                        exit()
                else:
                    print(f"Maybe Final: {sentence}")
            else:
                print(f"Interim Results: {sentence}")

        def on_metadata(self, metadata, **kwargs):
            print(f"Metadata: {metadata}")

        def on_speech_started(self, speech_started, **kwargs):
            print("Speech Started")

        def on_utterance_end(self, utterance_end, **kwargs):
            global is_finals, should_exit, conn
            if len(is_finals) > 0:
                utterance = " ".join(is_finals)
                print(f"Final Speech: {utterance}")

                # Pass the final transcription to the Gemini API
                response = agent.send_message(utterance).strip()

                # Displaying the reply
                print(f"Gemini Agent: {response}")

                # Query handling
                while response.startswith("SQL:"):
                    # Query the database using SQL
                    sql_response=''
                    try:
                        sql_response = 'SQL Response:\n' + str(pd.read_sql_query(response[5:].splitlines()[0], conn))
                    except Exception as e:
                        sql_response+='\n'+f"Error: {e}"
                    print(sql_response)
                    response = agent.send_message(sql_response).strip()
                    # Displaying the reply
                    print(f"Gemini Agent: {response}")
        
                # Remove EXIT if present in the response
                if response[-4:]=='EXIT':
                    response = response[:-6]
                    should_exit=True
                elif response[-8:]=='**EXIT**':
                    response = response[:-10]
                    should_exit=True
                    
                

                # Convert Gemini's response to audio
                output_file = 'output_audio.mp3'
                synthesize_audio_from_text(response, output_file)

                is_finals = []

                # Exit if EXIT command was detected
                if should_exit:
                    print("Detected EXIT command...")
                    exit()

        def on_close(self, close, **kwargs):
            global should_exit
            print("Connection Closed")
            should_exit=True

        def on_error(self, error, **kwargs):
            print(f"Handled Error: {error}")

        def on_unhandled(self, unhandled, **kwargs):
            print(f"Unhandled Websocket Message: {unhandled}")

        dg_connection.on(LiveTranscriptionEvents.Open, on_open)
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
        dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

        options: LiveOptions = LiveOptions(
            model="nova-2",
            language="en-IN",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            endpointing=800,
        )

        addons = {
            "no_delay": "true"
        }

        print("\n\nPress Ctrl+C or KeyboardInterrupt to stop recording...\n\n")
        if dg_connection.start(options, addons=addons) is False:
            print("Failed to connect to Deepgram")
            return

        # Audio streaming using PyAudio
        def audio_stream(dg_send_callback):
            global should_exit, conn
            chunk = 1024  # Size of each audio frame
            format = pyaudio.paInt16
            channels = 1
            rate = 16000  # Matches the `sample_rate` in LiveOptions

            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=format,
                channels=channels,
                rate=rate,
                input=True,
                frames_per_buffer=chunk,
            )

            #Starting call with opening statement
            play(AudioSegment.from_mp3('start_audio.mp3'))
            print("Gemini Agent: Hello! Thank you for calling our Service Department. How can I assist you today?")
            
            should_exit=False
            try:
                if should_exit:
                    stream.stop_stream()
                    stream.close()
                    audio.terminate()
                while should_exit==False:
                    data = stream.read(chunk, exception_on_overflow=False)
                    dg_send_callback(data)
            except KeyboardInterrupt:
                print("\nRecording stopped.")
            finally:
                stream.stop_stream()
                stream.close()
                audio.terminate()
                # Close the connection
                conn.close()

        # Start streaming audio to Deepgram
        audio_stream(dg_connection.send)

        # Indicate that we've finished
        dg_connection.finish()
        print("Finished")

    except Exception as e:
        print(f"Error: {e}")
        return

if __name__ == "__main__":
    main()
