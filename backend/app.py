# --- START OF FILE backend/app.py ---
from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS # Import CORS
import threading
import time
import os
import json
import logging
import sys
from dotenv import load_dotenv

# --- Add src directory to Python path ---
SRC_DIR = os.path.join(os.path.dirname(__file__), 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# --- Import the refactored library ---
try:
    from ayzzn_pro_library import AyzenScraper, AyzenConfig, ScraperCancelledError
    LIBRARY_AVAILABLE = True
except ImportError as e:
    LIBRARY_AVAILABLE = False
    AyzenScraper, AyzenConfig, ScraperCancelledError = None, None, None
    logging.basicConfig(level=logging.ERROR) # Basic config if logger fails below
    logging.error(f"FATAL: Failed to import ayzzn_pro_library from {SRC_DIR}: {e}", exc_info=True)
    logging.error("Backend cannot function without the scraper library.")
    # Exit? Or let Flask start and fail on endpoint calls? Let it start for now.

# --- Configuration ---
# Use environment variable for log level, default to INFO
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(threadName)s: %(message)s')

# Load .env file from the backend directory (where app.py resides)
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    logging.info(f"Loaded environment variables from {dotenv_path}")
else:
    logging.warning(f".env file not found at {dotenv_path}. Relying on system environment variables.")

# Define results folder relative to this script's location
RESULTS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), 'results'))
# Create results directory if it doesn't exist
if not os.path.exists(RESULTS_FOLDER):
    try:
        os.makedirs(RESULTS_FOLDER)
        logging.info(f"Created results directory: {RESULTS_FOLDER}")
    except OSError as e:
        logging.error(f"FATAL: Failed to create results directory {RESULTS_FOLDER}: {e}")
        exit(1) # Exit if cannot create results dir

app = Flask(__name__)

# --- Enable CORS ---
# Allow requests from any origin for development.
# For production, replace "*" with your specific frontend domain(s).
# Example: cors_origins = ["https://your-frontend-domain.com", "http://localhost:xxxx"]
cors_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(',')
CORS(app, resources={r"/api/*": {"origins": cors_origins}})
logging.info(f"CORS enabled for origins: {cors_origins}")


# --- Global State (Basic - Use Task Queue for production) ---
tasks = {} # task_id: {status: str, progress: int, log: list, result_files: list, thread: Thread|None, scraper_instance: AyzenScraper|None, config: dict}
task_id_counter = 0
task_lock = threading.Lock() # Protect access to the tasks dictionary

# --- Chatbot Personalities (defined on backend) ---
PERSONAS = {
    "1": {"name": "Alex (He/Him)", "prompt": "..."}, # Copy prompts from run_local.py
    "2": {"name": "Brenda (She/Her)", "prompt": "..."},
    "3": {"name": "Chris (They/Them)", "prompt": "..."},
    "4": {"name": "Diana (She/Her)", "prompt": "..."},
    "5": {"name": "Mike (He/Him)", "prompt": "..."},
    "default": {"name": "Ayzen Assistant", "prompt": "You are a helpful AI assistant for the Ayzen Scraper tool. Answer questions clearly and concisely."}
}
# Populate prompts (same as in run_local.py) - doing it inline for brevity in this example
PERSONAS["1"]["prompt"] = "You are Alex, a data-driven tech analyst specializing in lead generation for car dealerships. You're enthusiastic, knowledgeable about scraping technology and data analysis, and eager to help users maximize their lead potential. Respond in a friendly, slightly technical, and encouraging tone."
PERSONAS["2"]["prompt"] = "You are Brenda, a seasoned car dealership sales manager. You are highly experienced, pragmatic, and results-driven. You prefer direct communication and focus on actionable insights and effective sales strategies related to the scraped data. Respond in a professional, concise, and authoritative tone."
PERSONAS["3"]["prompt"] = "You are Chris, a helpful and patient support specialist for the Ayzen Scraper tool. You excel at explaining technical details in simple terms and guiding users through the tool's features and results. Respond in a clear, friendly, supportive, and easy-to-understand manner."
PERSONAS["4"]["prompt"] = "You are Diana, a meticulous and analytical researcher focused on data quality and interpretation. You are calm, precise, and detail-oriented. Provide accurate information based on the scraper's function and potential data, avoiding speculation. Respond in a formal, analytical, and slightly reserved tone."
PERSONAS["5"]["prompt"] = "You are Mike, an old-school car guy with decades of experience in the auto industry. You're practical, knowledgeable about dealerships and the market, and speak in a straightforward, maybe slightly informal or gruff, but helpful manner. Draw on practical industry insights when relevant. Respond like a seasoned pro giving practical advice."

# --- Google AI Setup (for Chat) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_model = None
google_ai_available_for_chat = False
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        # Basic model config, adjust as needed
        generation_config = {"temperature": 0.7,"top_p": 1,"top_k": 1}
        safety_settings = [ {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
        gemini_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash-latest',
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        google_ai_available_for_chat = True
        logging.info("Google AI Model initialized successfully for chat.")
    except ImportError:
        logging.warning("google-generativeai library not found, chat endpoint will be disabled.")
    except Exception as e:
        logging.error(f"Failed to initialize Google AI Model: {e}", exc_info=True)
else:
    logging.warning("GEMINI_API_KEY not found in environment, chat endpoint will be disabled.")


# --- Backend Logic: Running the Scraper ---
def run_scraper_wrapper(task_id):
    """ Wrapper function executed in a background thread for scraping. """
    global tasks
    scraper_instance, config = None, None
    final_status, final_message, final_files = "error", "Task initialization failed.", []

    try:
        with task_lock:
            if task_id not in tasks: logging.error(f"Task {task_id} missing at start."); return
            config_dict = tasks[task_id]['config']
            tasks[task_id]['status'] = 'running'
        logging.info(f"Task {task_id}: Scraper thread started.")

        # --- Create AyzenConfig Object ---
        tele_token = os.getenv("TELEGRAM_BOT_TOKEN")
        tele_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        # Use backend GEMINI_API_KEY, not passed from frontend
        gemini_key_backend = os.getenv("GEMINI_API_KEY")

        config = AyzenConfig(
            user_name=config_dict.get('user-name', 'Web User'),
            target_domains=int(config_dict.get('target-domains', 100)),
            keyword_source=config_dict.get('keyword-source', 'default'),
            ai_prompt=config_dict.get('ai-prompt'),
            max_threads=int(config_dict.get('max-threads', 4)),
            send_telegram=bool(config_dict.get('send-telegram', False)),
            output_dir=RESULTS_FOLDER, # Backend controls output path
            gemini_api_key=gemini_key_backend, # Use key from backend env
            telegram_token=tele_token,
            telegram_chat_id=tele_chat_id,
            status_callback=lambda p, m: update_status(task_id, p, m)
        )

        # --- Pre-run Checks ---
        update_status(task_id, 1, "Validating configuration...")
        if config.keyword_source == 'ai' and not config.gemini_api_key:
            raise ValueError("Config Error: AI keywords selected, but GEMINI_API_KEY is missing on backend.")
        if config.send_telegram and (not config.telegram_token or not config.telegram_chat_id):
             update_status(task_id, -1, "Warning: Telegram disabled (Token/Chat ID missing on backend).")
             config.send_telegram = False

        # --- Instantiate and Run Scraper ---
        if not AyzenScraper: raise RuntimeError("AyzenScraper library not loaded.")
        scraper_instance = AyzenScraper(config)
        with task_lock: tasks[task_id]['scraper_instance'] = scraper_instance
        logging.info(f"Task {task_id}: Starting scraper instance run...")
        final_message, final_files = scraper_instance.run()
        final_status = 'completed'
        logging.info(f"Task {task_id}: Scraper run finished normally.")

    except ScraperCancelledError as e:
        final_status, final_message = 'cancelled', str(e) or "Task cancelled."
        logging.warning(f"Task {task_id} cancelled: {final_message}")
        if scraper_instance: final_files = scraper_instance.files_saved
    except ValueError as e: # Config errors
        final_status, final_message = 'error', f"Config Error: {e}"
        logging.error(f"Task {task_id} config error: {e}")
        if scraper_instance: final_files = scraper_instance.files_saved
    except Exception as e: # Other runtime errors
        final_status, final_message = 'error', f"Runtime Error: {type(e).__name__} - {e}"
        logging.exception(f"Task {task_id} failed:")
        if scraper_instance: final_files = scraper_instance.files_saved
    finally:
        # --- Update Final Task Status ---
        with task_lock:
            if task_id in tasks:
                tasks[task_id]['status'] = final_status
                tasks[task_id]['progress'] = 100
                log_msg = f"Final Status: {final_status.upper()} - {final_message}"
                if final_message and (not tasks[task_id]['log'] or tasks[task_id]['log'][-1] != log_msg):
                     tasks[task_id]['log'].append(log_msg)
                tasks[task_id]['result_files'] = [os.path.basename(f) for f in final_files if f and RESULTS_FOLDER in os.path.abspath(f)]
                tasks[task_id]['scraper_instance'] = None # Release instance
                tasks[task_id]['thread'] = None
                logging.info(f"Task {task_id}: Final state set '{final_status}'.")
            else: logging.error(f"Task {task_id} disappeared before final update.")


def update_status(task_id, progress, message):
     """ Callback function passed to the scraper library to update task state. """
     with task_lock:
         if task_id in tasks:
             task = tasks[task_id]
             if task['status'] in ['queued', 'running']:
                  if progress != -1: task['progress'] = int(max(0, min(100, progress)))
             # Add timestamp at the backend log stage
             ts = time.strftime("%H:%M:%S")
             log_message = f"[{ts}] {message}"
             if message and (not task['log'] or task['log'][-1] != log_message):
                  task['log'].append(log_message)
                  # Optional: Limit log size
                  MAX_LOG = 150
                  if len(task['log']) > MAX_LOG: task['log'] = task['log'][-MAX_LOG:]
             # Minimal console logging from callback
             # logging.debug(f"Task {task_id} Update: P={progress} M='{message[:50]}...'")
         # else: logging.warning(f"Status update for non-existent task: {task_id}")


# --- API Endpoints ---

# Prefix all API routes for clarity
API_PREFIX = "/api/v1"

@app.route(f'{API_PREFIX}/start_scrape', methods=['POST'])
def start_scrape_api():
    """ API Endpoint: Starts a new scraping task. """
    global task_id_counter, tasks
    if not LIBRARY_AVAILABLE:
         logging.error("API call to /start_scrape failed: Library not available.")
         return jsonify({"error": "Backend scraper library is not available."}), 503 # Service Unavailable

    config_data = request.json
    if not config_data or not isinstance(config_data, dict):
        logging.warning(f"{API_PREFIX}/start_scrape: Invalid request data format.")
        return jsonify({"error": "Invalid request data format. JSON expected."}), 400

    with task_lock:
        task_id_counter += 1
        task_id = task_id_counter
        logging.info(f"API: Received scrape request (Task ID: {task_id}).")

        thread = threading.Thread(target=run_scraper_wrapper, args=(task_id,), name=f"ScraperTask-{task_id}")
        thread.daemon = True

        tasks[task_id] = {
            'status': 'queued', 'progress': 0, 'log': ["Task queued."],
            'result_files': [], 'config': config_data, 'thread': thread,
            'scraper_instance': None
        }
        try:
             thread.start()
             logging.info(f"API: Started background thread for task {task_id}")
        except RuntimeError as e:
             logging.exception(f"API: Failed to start thread for task {task_id}")
             tasks[task_id]['status'] = 'error'
             tasks[task_id]['log'].append(f"Error starting background thread: {e}")
             return jsonify({"error": "Failed to start background process"}), 500

    return jsonify({"message": "Scraping process initiated", "task_id": task_id}), 202


@app.route(f'{API_PREFIX}/status/<int:task_id>', methods=['GET'])
def get_status_api(task_id):
    """ API Endpoint: Gets the status of a specific task. """
    with task_lock:
        task = tasks.get(task_id)
        if task:
             # Return a subset of data relevant to the frontend
             task_data = {
                 "task_id": task_id, "status": task['status'],
                 "progress": task['progress'], "log": task['log'][-50:], # Limit log lines
                 "result_files": task.get('result_files', []) if task['status'] == 'completed' else []
             }
             response_data = task_data
        else: response_data = None

    if not response_data:
        abort(404, description=f"Task with ID {task_id} not found.")
    return jsonify(response_data)


@app.route(f'{API_PREFIX}/results/<path:filename>', methods=['GET'])
def download_file_api(filename):
    """ API Endpoint: Allows downloading of result files. """
    try:
        if not filename or '..' in filename or filename.startswith('/'):
             logging.warning(f"API: Invalid download request: {filename}")
             abort(400, description="Invalid filename requested.")

        safe_path = os.path.normpath(os.path.join(RESULTS_FOLDER, filename))
        if not safe_path.startswith(RESULTS_FOLDER) or not os.path.isfile(safe_path):
            logging.warning(f"API: File not found or access denied: {filename} -> {safe_path}")
            abort(404, description="Result file not found or access denied.")

        logging.info(f"API: Serving file download: {filename}")
        return send_from_directory(RESULTS_FOLDER, filename, as_attachment=True)
    except Exception as e:
        logging.exception(f"API: Error serving file download '{filename}'")
        abort(500, description="Error serving file.")


@app.route(f'{API_PREFIX}/cancel/<int:task_id>', methods=['POST'])
def cancel_task_api(task_id):
    """ API Endpoint: Attempts to cancel a running task. """
    instance_to_cancel, task_status, task_exists = None, None, False
    with task_lock:
        task = tasks.get(task_id)
        if task:
            task_exists, task_status = True, task['status']
            if task_status in ['queued', 'running']:
                instance_to_cancel = task.get('scraper_instance')
                task['status'] = 'cancelling'
                update_status(task_id, task['progress'], "Cancellation requested by user...")
                logging.info(f"API: Cancellation requested for task {task_id} (Status: {task_status})")
            else: logging.info(f"API: Cancel request ignored for task {task_id} (Status: {task_status})")
        else: logging.warning(f"API: Cancel request for non-existent task ID: {task_id}")

    if instance_to_cancel:
        try:
            instance_to_cancel.cancel()
            logging.info(f"API: Cancellation signal sent to instance for task {task_id}")
            return jsonify({"message": "Cancellation signal sent."}), 200
        except Exception as e:
             logging.exception(f"API: Error calling cancel() for task {task_id}")
             with task_lock:
                 if task_id in tasks: tasks[task_id]['status'] = 'error'
             return jsonify({"error": "Failed to send cancellation signal"}), 500
    elif task_exists and task_status == 'queued':
        with task_lock:
             if task_id in tasks:
                  tasks[task_id]['status']='cancelled'; tasks[task_id]['progress']=100
                  tasks[task_id]['log'].append("Task cancelled while queued.")
                  logging.info(f"API: Task {task_id} cancelled while queued.")
        return jsonify({"message": "Task cancelled while queued."}), 200
    elif task_exists:
        return jsonify({"message": f"Task already {task_status}."}), 400
    else: abort(404, description=f"Task {task_id} not found.")


@app.route(f'{API_PREFIX}/chat', methods=['POST'])
def chat_api():
    """ API Endpoint: Handles chatbot interactions. """
    if not google_ai_available_for_chat or not gemini_model:
        logging.warning("API: /chat endpoint called but Google AI is not available/configured.")
        return jsonify({"error": "Chat feature is not available."}), 503

    data = request.json
    user_message = data.get("message")
    persona_id = str(data.get("persona_id", "default")) # Get persona ID from request

    if not user_message:
        return jsonify({"error": "No message provided."}), 400

    # Get the selected persona prompt, fallback to default
    persona = PERSONAS.get(persona_id, PERSONAS["default"])
    system_prompt = persona["prompt"]
    persona_name = persona["name"]
    logging.info(f"API: Received chat message. Persona: {persona_name} ({persona_id})")

    try:
        # Construct the prompt for the AI, including the system instruction
        # For simple back-and-forth, just prepend system prompt to user message.
        # For multi-turn, the frontend would need to send history, or backend manage it.
        full_prompt = f"{system_prompt}\n\nHuman: {user_message}\n\nAssistant:"

        # Call the Gemini API
        response = gemini_model.generate_content(full_prompt)

        ai_response_text = ""
        # Handle potential blocked responses or empty parts
        if response and hasattr(response, 'text'):
             ai_response_text = response.text.strip()
        elif response and hasattr(response, 'prompt_feedback') and getattr(response, 'prompt_feedback').block_reason:
             block_reason = response.prompt_feedback.block_reason
             ai_response_text = f"(My response was blocked due to safety reasons: {block_reason}. Please try rephrasing.)"
             logging.warning(f"Chat response blocked for task_id (N/A) due to: {block_reason}")
        else:
             ai_response_text = "(I couldn't generate a response for that.)"
             logging.warning(f"Chat response was empty or invalid. Raw response: {response}")

        logging.info(f"API: Sending AI response (Persona: {persona_name}, Length: {len(ai_response_text)}).")
        return jsonify({"reply": ai_response_text})

    except Exception as e:
        logging.exception("API: Error during Google AI call in /chat endpoint:")
        return jsonify({"error": f"An error occurred while contacting the AI assistant: {e}"}), 500

# --- Optional: Simple Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
     # Basic health check, doesn't use API prefix or CORS
     return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()}), 200


if __name__ == '__main__':
    print("--- Ayzen Scraper Backend API ---")
    if not LIBRARY_AVAILABLE:
        print("\n!!! CRITICAL ERROR: Ayzen Scraper library failed to load. API endpoints will likely fail. !!!\n")
    if not google_ai_available_for_chat:
        print("\n!!! WARNING: Google AI (Gemini) not configured or library missing. Chat API endpoint (/api/v1/chat) will be disabled. !!!\n")
    print(f"Results will be stored in: {RESULTS_FOLDER}")
    print(f"Allowed CORS origins: {cors_origins}")
    print(f"Log Level: {log_level}")
    print(f"Ensure a '.env' file exists in '{os.path.dirname(__file__)}' with your API keys.")
    print("---")
    print("Starting Flask server...")
    # Use waitress or gunicorn for production instead of Flask's built-in server
    # Example: waitress-serve --host 127.0.0.1 --port 5001 app:app
    # For development:
    app.run(debug=False, port=5001, host='127.0.0.1', threaded=True)

# --- END OF FILE backend/app.py ---
