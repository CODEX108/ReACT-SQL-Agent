import google.generativeai as genai
import os

# Set your API key
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("Error: GOOGLE_API_KEY environment variable not set.")
    print("Please set it in your terminal before running this script.")
else:
    genai.configure(api_key=api_key)

    print("Listing all available models for your API key...")
    print("="*40)

    try:
        for model in genai.list_models():
            # We only care about models that can be used for chatting
            if 'generateContent' in model.supported_generation_methods:
                print(f"Model name: {model.name}")
                print(f"   Description: {model.description}\n")

    except Exception as e:
        print(f"\nAn error occurred: {e}")
        print("This could be an API key issue or a connection problem.")

print("="*40)
print("Find a model name above (like 'gemini-1.5-flash-latest' or 'models/gemini-pro') and copy it.")