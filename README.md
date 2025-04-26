# Ayzen Scraper - Web App (Static Frontend + API Backend)

This project provides a web interface for the Ayzen Scraper tool, designed for a two-part deployment: a static frontend and a separate API backend.

**Architecture:**

1.  **Frontend (`webapp/`):** Contains HTML, CSS, and JavaScript files. This part is designed to be hosted on simple static web hosting (like a "business plan"). It **does not** run Python or handle sensitive operations. It communicates with the backend via API calls.
2.  **Backend (`backend/`):** Contains a Python Flask application (`app.py`) and the core scraping logic (`src/ayzzn_pro_library.py`). This part **must** be hosted on a server capable of running Python (e.g., VPS, Heroku, Render, PythonAnywhere). It handles all scraping tasks, AI interactions, Telegram notifications, and secure API key management.

## Features

*   Web interface to configure and start scraping tasks.
*   Real-time status updates and logging via API polling.
*   Download links for generated result files (served by the backend).
*   Optional AI keyword generation (processed by backend).
*   Optional Telegram reports (sent by backend).
*   Integrated AI Chatbot widget using Google Gemini (powered by backend).
*   Selectable AI chatbot personalities.
*   Dark/Light mode theme toggle.

## Deployment Steps

### Part 1: Deploying the Backend API

1.  **Choose Hosting:** Select a hosting provider that supports Python applications (e.g., Render, PythonAnywhere, Heroku (with caveats), VPS like DigitalOcean/Linode/AWS EC2).
2.  **Upload Backend Code:** Copy the entire `backend/` directory contents to your chosen hosting server.
3.  **Set up Environment:**
    *   Navigate to the `backend/` directory on the server.
    *   Create a Python virtual environment (`python -m venv venv` && `source venv/bin/activate`).
    *   Install dependencies: `pip install -r requirements.txt`.
    *   **Crucially:** Create a `.env` file (from `.env_example`) in the `backend/` directory and add your `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. Ensure this file has restrictive permissions.
    *   **(Production):** Configure allowed origins in `.env` (e.g., `ALLOWED_ORIGINS=https://your-frontend-domain.com`). For development, you might use `ALLOWED_ORIGINS=*` (less secure).
4.  **Run the Backend:**
    *   **Development/Testing:** `python app.py`
    *   **Production:** Use a production-grade WSGI server like Gunicorn or Waitress. Example with Gunicorn:
        ```bash
        # Install gunicorn: pip install gunicorn
        # Run (adjust workers as needed):
        gunicorn --workers 4 --bind 0.0.0.0:5001 app:app
        ```
        (You might need to configure a reverse proxy like Nginx in front of Gunicorn for SSL, load balancing, etc.)
5.  **Note the Backend URL:** Make sure you know the publicly accessible URL where your backend API is running (e.g., `https://your-backend-app.onrender.com`, `http://your-vps-ip:5001`).

### Part 2: Deploying the Frontend

1.  **Configure API URL:**
    *   **Edit the file `webapp/js/script.js`**.
    *   Find the line `const BACKEND_API_BASE_URL = '/api/v1';`.
    *   **Change `/api/v1`** to the **full URL of your running backend API**, including the `/api/v1` prefix. For example:
        ```javascript
        const BACKEND_API_BASE_URL = 'https://your-backend-app.onrender.com/api/v1';
        // OR for local testing (if backend runs on port 5001):
        // const BACKEND_API_BASE_URL = 'http://localhost:5001/api/v1';
        ```
    *   Save the file.
2.  **Upload Frontend Files:** Upload the *entire contents* of the `webapp/` directory (including `index.html`, `css/`, `js/`) to your static web hosting provider (the "business plan" hosting).
3.  **Access the Web App:** Navigate to the URL provided by your static hosting provider where you uploaded the `webapp` files.

## Using the Web App

1.  Open the frontend URL in your browser.
2.  Configure the desired scraping parameters in the "Configure Scraper Task" section.
3.  Click "Start Scraping Task".
4.  Monitor progress in the "Task Status" section.
5.  Once completed, download links for results will appear.
6.  Click the chat icon (<i class="fas fa-comment-dots"></i>) in the bottom right to open the AI Assistant, select a persona, and ask questions.

## Important Considerations

*   **Security:** Never expose your API keys in the frontend JavaScript. They MUST reside only on the backend server within the `.env` file. Restrict CORS origins in production.
*   **Backend Hosting Costs:** Running a Python backend server typically incurs costs beyond basic static hosting plans.
*   **Scalability:** The provided backend uses simple threading. For high traffic or many concurrent scraping tasks, consider a more robust task queue system (like Celery with Redis).
*   **Error Handling:** Review and enhance error handling in both frontend and backend for production robustness.

