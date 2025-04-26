// --- START OF FILE webapp/js/script.js ---
document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const themeToggle = document.getElementById('theme-toggle');
    const body = document.body;
    const scrapeForm = document.getElementById('scrape-form');
    const startButton = document.getElementById('start-button');
    const cancelButton = document.getElementById('cancel-button');
    const aiOptionsDiv = document.getElementById('ai-options');
    const keywordSourceRadios = document.querySelectorAll('input[name="keyword-source"]');
    // Status & Results Area
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    const resultsSection = document.getElementById('results-section');
    const resultsSummary = document.getElementById('results-summary');
    const downloadLinksDiv = document.getElementById('download-links');
    const progressBarContainer = document.getElementById('progress-bar-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const taskIdDisplay = document.getElementById('task-id-display');
    // Chatbot Elements
    const chatbotToggle = document.getElementById('chatbot-toggle');
    const chatbotWidget = document.getElementById('chatbot-widget');
    const chatbotClose = document.getElementById('chatbot-close');
    const chatbotMessages = document.getElementById('chatbot-messages');
    const chatbotInput = document.getElementById('chatbot-input');
    const chatbotSend = document.getElementById('chatbot-send');
    const personaSelect = document.getElementById('persona-select');
    const typingIndicator = document.getElementById('chatbot-typing-indicator');


    // --- Configuration ---
    // !!! CRITICAL: SET THIS TO YOUR ACTUAL BACKEND API URL !!!
    // Examples: 'https://your-backend.onrender.com/api/v1', 'http://your-vps-ip:5001/api/v1', 'http://localhost:5001/api/v1' (for local testing)
    const BACKEND_API_BASE_URL = '/api/v1'; // Default for proxy setup, CHANGE THIS FOR PRODUCTION
    const POLLING_INTERVAL_MS = 4000; // Check status every 4 seconds

    // Chatbot Personalities (match backend definition keys)
    const CHATBOT_PERSONAS = {
        "default": "Ayzen Assistant",
        "1": "Alex (Analyst)",
        "2": "Brenda (Manager)",
        "3": "Chris (Support)",
        "4": "Diana (Researcher)",
        "5": "Mike (Veteran)"
    };


    // --- Global State ---
    let currentTaskId = null;
    let pollingIntervalId = null;
    let isChatbotOpen = false;
    let isChatbotSending = false;
    const loggedMessages = new Set(); // Track logged messages to avoid duplicates


    // --- Initialization ---
    function initialize() {
        setupEventListeners();
        applyStoredTheme();
        populatePersonaSelector();
        handleKeywordSourceChange(); // Set initial form state
        resetScraperUI(); // Start clean
        resetChatbotUI();
        console.log("Ayzen Scraper UI Initialized. Backend API Target:", BACKEND_API_BASE_URL);
        // Verify backend URL is set
        if (BACKEND_API_BASE_URL === '/api/v1') { // Check if default is still set
            addLogMessage("⚠️ WARNING: Backend API URL is not configured in js/script.js. Using default '/api/v1'. Update 'BACKEND_API_BASE_URL' for production.", "error", true);
        }
    }

    // --- Event Listeners Setup ---
    function setupEventListeners() {
        themeToggle.addEventListener('click', toggleTheme);
        scrapeForm.addEventListener('submit', handleScrapeFormSubmit);
        if (cancelButton) cancelButton.addEventListener('click', handleCancelTask);
        keywordSourceRadios.forEach(radio => radio.addEventListener('change', handleKeywordSourceChange));
        // Chatbot listeners
        chatbotToggle.addEventListener('click', toggleChatbot);
        chatbotClose.addEventListener('click', toggleChatbot);
        chatbotSend.addEventListener('click', sendChatMessage);
        chatbotInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendChatMessage(); });
    }

    // --- Theme Handling ---
    function applyStoredTheme() {
        const storedTheme = localStorage.getItem('theme');
        const isDarkMode = storedTheme ? storedTheme === 'dark' : body.classList.contains('dark-mode'); // Default to body class if no storage
        body.classList.toggle('dark-mode', isDarkMode);
        themeToggle.setAttribute('aria-label', isDarkMode ? 'Toggle Light Mode' : 'Toggle Dark Mode');
    }
    function toggleTheme() {
        body.classList.toggle('dark-mode');
        const isDarkMode = body.classList.contains('dark-mode');
        localStorage.setItem('theme', isDarkMode ? 'dark' : 'light');
        applyStoredTheme(); // Update button label
    }

    // --- Form Handling ---
    function handleKeywordSourceChange() {
        const selectedSource = document.querySelector('input[name="keyword-source"]:checked')?.value;
        aiOptionsDiv.classList.toggle('hidden', selectedSource !== 'ai');
        // Note: File upload input handling is omitted as it wasn't fully implemented
    }

    // --- API Call Helper ---
    async function apiFetch(endpoint, options = {}) {
        const url = `${BACKEND_API_BASE_URL}${endpoint}`;
        try {
            const response = await fetch(url, options);
            // Try parsing JSON even for non-ok responses, as backend might send error details
            const data = await response.json().catch(() => null); // Gracefully handle non-JSON responses

            if (!response.ok) {
                // Construct a meaningful error message
                const errorMessage = data?.error || data?.message || `HTTP error ${response.status} - ${response.statusText}`;
                console.error(`API Error for ${endpoint}: ${errorMessage}`, data);
                throw new Error(errorMessage); // Throw error to be caught by caller
            }
            return data; // Return parsed JSON data on success
        } catch (error) {
            console.error(`Network or API error for ${endpoint}:`, error);
            // Re-throw the error so the calling function knows it failed
            throw new Error(`Network or API error: ${error.message || 'Failed to fetch'}`);
        }
    }

    // --- Scraper Logic ---
    async function handleScrapeFormSubmit(e) {
        e.preventDefault();
        if (startButton.disabled) return;

        resetScraperUI();
        startButton.disabled = true;
        startButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Starting...';
        if(cancelButton) cancelButton.classList.add('hidden');

        statusSection.classList.remove('hidden');
        statusSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        addLogMessage("Initializing scraper task...", "info", true); // Clear previous logs
        taskIdDisplay.textContent = '';

        const formData = new FormData(scrapeForm);
        const configData = Object.fromEntries(formData.entries());
        configData['send-telegram'] = formData.has('send-telegram');
        configData['target-domains'] = parseInt(formData.get('target-domains'), 10) || 100;
        configData['max-threads'] = parseInt(formData.get('max-threads'), 10) || 4;
        // Frontend doesn't need/send API keys

        try {
            const result = await apiFetch('/start_scrape', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configData)
            });

            currentTaskId = result.task_id;
            taskIdDisplay.textContent = `Task ID: ${currentTaskId}`;
            addLogMessage(`Task started successfully (ID: ${currentTaskId}). Monitoring progress...`, "success");
            startButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
            progressBarContainer.classList.remove('hidden');
            updateProgress(0);
            startPolling(currentTaskId);
            if (cancelButton) {
                cancelButton.classList.remove('hidden');
                cancelButton.disabled = false;
            }

        } catch (error) {
            addLogMessage(`❌ Error starting task: ${error.message}`, "error", false); // Don't clear logs on error
            resetScraperUI(); // Reset buttons etc.
        }
    }

    async function handleCancelTask() {
        if (!currentTaskId || cancelButton.disabled) return;

        addLogMessage(`Attempting to cancel task ${currentTaskId}...`, "warning");
        cancelButton.disabled = true;
        cancelButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Cancelling...';

        try {
            const result = await apiFetch(`/cancel/${currentTaskId}`, { method: 'POST' });
            addLogMessage(result.message || `Cancellation request sent for task ${currentTaskId}.`, "info");
            // Polling will handle the final state change (cancelled/error) and UI reset
        } catch (error) {
            addLogMessage(`❌ Error requesting cancellation: ${error.message}`, "error");
            // Re-enable cancel button ONLY if the cancel request itself failed
            cancelButton.disabled = false;
            cancelButton.innerHTML = '<i class="fas fa-stop"></i> Cancel Task';
        }
    }

    // --- Polling Logic ---
    function startPolling(taskId) {
        stopPolling(); // Clear existing interval
        addLogMessage(`Starting status polling for Task ID: ${taskId}`, "debug");
        pollStatus(taskId); // Poll immediately
        pollingIntervalId = setInterval(() => pollStatus(taskId), POLLING_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollingIntervalId) {
            clearInterval(pollingIntervalId);
            addLogMessage(`Stopped status polling for Task ID: ${currentTaskId}`, "debug");
            pollingIntervalId = null;
        }
    }

    async function pollStatus(taskId) {
        if (!taskId) { stopPolling(); return; }
        console.debug(`Polling status for task ${taskId}...`); // Keep console log concise

        try {
            const data = await apiFetch(`/status/${taskId}`);
            console.debug("Received status:", data);

            updateProgress(data.progress);
            updateStatusLog(data.log);

            const finishedStates = ['completed', 'error', 'cancelled'];
            if (finishedStates.includes(data.status)) {
                stopPolling();
                addLogMessage(`Task finished with status: ${data.status.toUpperCase()}`, data.status === 'error' ? "error" : "info");
                displayResults(data);
                if (data.status === 'completed') {
                    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
                resetScraperButtons(); // Reset buttons, keep logs/results visible
                currentTaskId = null; // Clear task ID
            } else {
                startButton.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Running (${data.progress}%)`;
            }
        } catch (error) {
            // Handle polling errors (e.g., network down, backend restarted, 404)
            if (error.message.includes("404")) { // Specific handling for 404
                addLogMessage(`Error: Task ${taskId} not found on server. Stopping monitor.`, "error");
                resetScraperUI();
            } else {
                addLogMessage(`⚠️ Polling error: ${error.message}. Retrying...`, "warning");
                // Continue polling for transient errors
            }
        }
    }

    // --- Scraper UI Update Functions ---
    function resetScraperUI() {
        stopPolling();
        startButton.disabled = false;
        startButton.innerHTML = '<i class="fas fa-play"></i> Start Scraping Task';
        if(cancelButton) {
            cancelButton.classList.add('hidden');
            cancelButton.disabled = true;
        }
        statusSection.classList.add('hidden');
        resultsSection.classList.add('hidden');
        progressBarContainer.classList.add('hidden');
        updateProgress(0);
        taskIdDisplay.textContent = '';
        currentTaskId = null;
        // Don't clear logs here, only on new start action
    }

    function resetScraperButtons() {
         // Only resets buttons after a task finishes, leaves logs/results visible
        startButton.disabled = false;
        startButton.innerHTML = '<i class="fas fa-play"></i> Start Scraping Task';
        if(cancelButton) {
            cancelButton.classList.add('hidden');
            cancelButton.disabled = true;
        }
    }


    function updateProgress(percentage) {
        const clamped = Math.max(0, Math.min(100, percentage));
        progressBar.style.width = `${clamped}%`;
        progressText.textContent = `${Math.round(clamped)}%`;
    }

    function updateStatusLog(logMessagesFromServer) {
        if (!Array.isArray(logMessagesFromServer)) return;
        logMessagesFromServer.forEach(logLine => addLogMessage(logLine, "log"));
    }

    function addLogMessage(message, type = "log", clearLogs = false) {
        // type: log, info, success, warning, error, debug
        if (clearLogs) {
            statusLog.innerHTML = '';
            loggedMessages.clear();
        }

        // Avoid adding exact duplicate messages consecutively
        if (loggedMessages.has(message)) return;
        loggedMessages.add(message);
        // Optional: Limit size of loggedMessages Set to prevent memory issues

        const logEntry = document.createElement('div');
        let iconClass = 'fa-info-circle';
        let color = 'inherit';

        // Determine icon/color based on type or message content
        if (type === "error" || message.includes("❌") || message.toLowerCase().includes("error")) {
            iconClass = 'fa-times-circle'; color = 'var(--danger-color)'; type = 'error';
        } else if (type === "success" || message.includes("✅") || message.includes("🏁")) {
            iconClass = 'fa-check-circle'; color = 'var(--success-color)'; type = 'success';
        } else if (type === "warning" || message.includes("⚠️")) {
            iconClass = 'fa-exclamation-triangle'; color = 'var(--warning-color)'; type = 'warning';
        } else if (type === "debug") {
            iconClass = 'fa-bug'; color = 'var(--secondary-color)';
        } else { // log, info
             iconClass = 'fa-info-circle'; color = 'var(--secondary-color)'; type = 'info';
        }

        // Extract timestamp if present (backend adds [HH:MM:SS])
        const timeMatch = message.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*/);
        const displayMessage = timeMatch ? message.substring(timeMatch[0].length) : message;
        const displayTimestamp = timeMatch ? timeMatch[1] : new Date().toLocaleTimeString();

        logEntry.innerHTML = `<span class="log-time">[${displayTimestamp}]</span> <i class="fas ${iconClass}" style="color:${color};"></i> <span class="log-message">${displayMessage}</span>`;
        logEntry.classList.add(`log-type-${type}`); // Add class for potential further styling

        statusLog.appendChild(logEntry);
        // Auto-scroll only if near the bottom
        if (statusLog.scrollHeight - statusLog.scrollTop - statusLog.clientHeight < 100) {
            statusLog.scrollTop = statusLog.scrollHeight;
        }
    }


    function displayResults(data) {
        resultsSection.classList.remove('hidden');
        resultsSummary.textContent = data.log && data.log.length > 0 ? data.log[data.log.length-1].replace(/^\[.*?\]\s*/, '') : "Task Completed."; // Use last log as summary

        downloadLinksDiv.innerHTML = '';
        if (data.result_files && data.result_files.length > 0) {
            data.result_files.forEach(filename => {
                const fileUrl = `${BACKEND_API_BASE_URL}/results/${encodeURIComponent(filename)}`;
                const link = document.createElement('a');
                link.href = fileUrl;
                link.classList.add('btn', 'btn-download');
                link.innerHTML = `<i class="fas fa-download"></i> ${filename}`;
                link.target = '_blank';
                downloadLinksDiv.appendChild(link);
            });
        } else {
            downloadLinksDiv.innerHTML = '<p>No result files were reported for this task.</p>';
        }
    }

    // --- Chatbot Logic ---
    function populatePersonaSelector() {
        Object.entries(CHATBOT_PERSONAS).forEach(([id, name]) => {
             if (id === "default") return; // Skip default for selection
             const option = document.createElement('option');
             option.value = id;
             option.textContent = name;
             personaSelect.appendChild(option);
        });
        // Add default option if needed, or just use the first one
        // const defaultOption = document.createElement('option');
        // defaultOption.value = "default"; defaultOption.textContent = CHATBOT_PERSONAS["default"];
        // personaSelect.prepend(defaultOption); // Add default at top
        personaSelect.value = "3"; // Default to Chris (Support)
    }

    function toggleChatbot() {
        isChatbotOpen = !isChatbotOpen;
        chatbotWidget.classList.toggle('hidden', !isChatbotOpen);
        chatbotToggle.innerHTML = isChatbotOpen ? '<i class="fas fa-times"></i>' : '<i class="fas fa-comment-dots"></i>'; // Change icon
        if (isChatbotOpen && chatbotMessages.children.length === 0) {
            // Add initial greeting only if chat is empty
            addChatMessage("Ayzen Assistant", "Hello! How can I help you today? You can select a personality from the dropdown.", "ai");
        }
         if(isChatbotOpen) chatbotInput.focus(); // Focus input when opened
    }

    function addChatMessage(sender, message, type = "ai", personaId = null) {
         // type = 'ai' or 'user'
        const messageElement = document.createElement('div');
        messageElement.classList.add('chatbot-message', type);

        let senderName = sender;
        if (type === 'ai') {
             // Use selected persona name or default if none provided
             const currentPersonaId = personaId || personaSelect.value || "default";
             senderName = CHATBOT_PERSONAS[currentPersonaId] || CHATBOT_PERSONAS["default"];
        }

        messageElement.innerHTML = `<strong>${senderName}</strong> ${message}`; // Simple text display
        chatbotMessages.appendChild(messageElement);
        // Scroll to the bottom of the messages
        chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
    }

    async function sendChatMessage() {
        const message = chatbotInput.value.trim();
        if (!message || isChatbotSending) return;

        isChatbotSending = true;
        chatbotInput.value = '';
        chatbotInput.disabled = true;
        chatbotSend.disabled = true;
        typingIndicator.classList.remove('hidden');

        addChatMessage("You", message, "user");

        const selectedPersonaId = personaSelect.value || "default"; // Get selected persona

        try {
            const data = await apiFetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message, persona_id: selectedPersonaId }) // Send persona ID
            });
            addChatMessage("AI", data.reply, "ai", selectedPersonaId); // Pass personaId to display correct name
        } catch (error) {
            addChatMessage("System", `Error: Could not get response from AI assistant. ${error.message}`, "ai");
        } finally {
            isChatbotSending = false;
            chatbotInput.disabled = false;
            chatbotSend.disabled = false;
            typingIndicator.classList.add('hidden');
            chatbotInput.focus(); // Re-focus input
        }
    }

    function resetChatbotUI() {
        chatbotMessages.innerHTML = '';
        chatbotInput.value = '';
        if (isChatbotOpen) { // Close chatbot if resetting UI
            toggleChatbot();
        }
    }

    // --- Run Initialization ---
    initialize();

});
// --- END OF FILE webapp/js/script.js ---
