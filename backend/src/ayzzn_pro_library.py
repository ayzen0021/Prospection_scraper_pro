# --- START OF FILE backend/src/ayzzn_pro_library.py ---
import os
import re
import sys
import time
import json
import csv
import random
from datetime import datetime
from urllib.parse import urlparse, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
import signal # Keep for potential direct use? Maybe not needed if cancelled via flag
import requests
from bs4 import BeautifulSoup
import phonenumbers
import google.generativeai as genai
from dotenv import load_dotenv # Used here only if run standalone for testing
import logging # Use logging instead of prints for library

# --- Setup basic logging for the library ---
# This allows the main script to configure the root logger
lib_logger = logging.getLogger(__name__)
# Prevent duplicate handlers if root logger is already configured
if not lib_logger.hasHandlers():
     lib_logger.addHandler(logging.NullHandler()) # Add NullHandler to avoid "No handler found" warnings

# --- Attempt to import optional search library ---
try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDGS = None
    DDG_AVAILABLE = False
    lib_logger.warning("duckduckgo_search library not found. DDG search will be disabled.")
    lib_logger.warning("Install using: pip install duckduckgo_search")

# --- Constants ---
DEFAULT_KEYWORDS = ["used car dealership near me", "buy car online"]
NUM_KEYWORDS_TO_GENERATE = 30 # Default if AI is used
AI_MODEL_NAME = "gemini-1.5-flash-latest"
DEFAULT_AI_PROMPT_TEMPLATE = (
    f"Generate a diverse list of exactly {{num_keywords}} unique search engine keywords "
    "that someone would use to find used car dealerships across the USA. "
    "Include variations for locations, dealership types, inventory, financing, and general searches. "
    "Format as a plain text list, one keyword per line, no extra text."
)

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SEARCH = 2.5
REQUEST_DELAY_SITE = 1.0
RESULTS_PER_PAGE_SCRAPE = 10 # For manual scraping (if ever re-enabled)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36'
]
DEALER_CENTER_INDICATORS = [
    r'dealer[-_\s]?center', r'dchublinks', r'dcimg',
    r'powered\s+by\s+dealer\s*center', r'dealercenter\.com',
    r'/Inventory/Listing/DCH', r'widget\.dealercenter\.com',
    r'images\.dealercenter\.com'
]

class ScraperCancelledError(Exception):
    """Custom exception for graceful cancellation."""
    pass

class AyzenConfig:
    """Configuration class for the scraper library."""
    def __init__(self,
                 user_name="Web User", # Default for web context
                 target_domains=100,
                 keyword_source='default', # 'default', 'ai', 'list'
                 keywords_list=None, # Used if source is 'list'
                 ai_prompt=None, # Custom AI prompt
                 max_threads=4,
                 send_telegram=False,
                 output_dir="results", # Relative path used by backend
                 gemini_api_key=None,
                 telegram_token=None,
                 telegram_chat_id=None,
                 status_callback=None, # Function to report progress/status
                 run_timestamp=None): # Allow passing timestamp for consistency

        self.user_name = user_name
        self.target_domains = int(target_domains)
        self.keyword_source = keyword_source
        self.keywords_list = keywords_list or []
        self.ai_prompt = ai_prompt
        self.max_threads = int(max_threads)
        self.send_telegram = send_telegram
        # Output dir is relative to backend server's execution path
        self.output_dir = output_dir
        self.gemini_api_key = gemini_api_key
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        # Default callback logs to the library's logger if none provided
        self.status_callback = status_callback or self._default_status_callback

        # Derived filenames based on timestamp when scraper *starts*
        self.run_timestamp = run_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        # Ensure output directory exists (backend responsibility)
        # os.makedirs(self.output_dir, exist_ok=True) # Let backend handle dir creation
        self.contacts_json_file = os.path.join(self.output_dir, f"ayzen_contacts_{self.run_timestamp}.jsonl")
        self.contacts_csv_file = os.path.join(self.output_dir, f"ayzen_contacts_{self.run_timestamp}.csv")
        self.valid_domains_file = os.path.join(self.output_dir, f"ayzen_valid_domains_{self.run_timestamp}.txt")
        self.invalid_domains_file = os.path.join(self.output_dir, f"ayzen_invalid_domains_{self.run_timestamp}.txt")
        self.keywords_file = os.path.join(self.output_dir, f"ayzen_keywords_used_{self.run_timestamp}.json")

    def _default_status_callback(self, progress, message):
        """Default status handler logs messages."""
        if progress == -1: # Log signal/status messages
             lib_logger.info(message)
        else: # Log all messages if needed for debugging
            lib_logger.info(f"Status ({progress}%): {message}")


class AyzenScraper:
    """Encapsulates the scraping logic."""

    def __init__(self, config: AyzenConfig):
        self.config = config
        self.is_cancelled = False
        self.all_domains_found = set()
        self.valid_dealer_sites = []
        self.invalid_sites = []
        self.extracted_contacts_count = 0
        self.files_saved = [] # Keep track of generated files

        # Backend ensures output dir exists before calling run
        lib_logger.info(f"AyzenScraper initialized. Output directory target: {self.config.output_dir}")
        lib_logger.info(f"Run timestamp: {self.config.run_timestamp}")


    def cancel(self):
        """Signals the scraper to stop gracefully."""
        if not self.is_cancelled: # Prevent multiple cancel messages
            self.is_cancelled = True
            self._status_update(-1, "Cancellation signal received. Attempting graceful shutdown...")
            # No TQDM bars in library mode

    def _check_cancel(self):
        """Checks the cancellation flag and raises an error if set."""
        if self.is_cancelled:
            raise ScraperCancelledError("Scraping process cancelled by user.")

    def _status_update(self, progress_percent, message):
        """Reports status via the callback function."""
        if progress_percent != -1:
            progress_percent = max(0, min(100, int(progress_percent)))
        self.config.status_callback(progress_percent, message)

    # --- Helper Functions ---
    def _get_random_headers(self):
        return {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1',
        }

    def _safe_request(self, url, method='GET', **kwargs):
        """Makes an HTTP request with error handling and cancellation check."""
        self._check_cancel() # Check before making request
        headers = self._get_random_headers()
        final_headers = {**headers, **kwargs.get('headers', {})}
        kwargs['headers'] = final_headers
        kwargs.setdefault('timeout', REQUEST_TIMEOUT)
        kwargs.setdefault('allow_redirects', True)
        try: # Disable SSL warnings safely
             requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
        except AttributeError: pass
        kwargs.setdefault('verify', False)

        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            self._check_cancel()
            return response
        except requests.exceptions.Timeout:
            lib_logger.warning(f"Timeout requesting {url}")
        except requests.exceptions.TooManyRedirects:
            lib_logger.warning(f"Redirect loop for {url}")
        except requests.exceptions.SSLError as e:
             lib_logger.warning(f"SSL Error for {url}: {e}")
        except requests.exceptions.ConnectionError as e:
            lib_logger.warning(f"Connection Error for {url}: {e}")
        except requests.exceptions.HTTPError as e:
            lib_logger.warning(f"HTTP Error {e.response.status_code} for {url}")
        except requests.exceptions.RequestException as e:
            lib_logger.warning(f"Request failed for {url}: {type(e).__name__} - {e}")
        except Exception as e:
            lib_logger.error(f"Unexpected error during request for {url}: {type(e).__name__} - {e}", exc_info=False)
        return None

    def _send_telegram(self, message_type, **kwargs):
        """Sends messages or documents to Telegram if configured."""
        if not self.config.send_telegram or not self.config.telegram_token or not self.config.telegram_chat_id:
            return
        time.sleep(0.1) # Small delay

        try:
            if message_type == "message":
                text = kwargs.get("text", "")
                if not text: return
                max_len = 4096
                text_parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
                url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"
                for i, part in enumerate(text_parts):
                    payload = {'chat_id': self.config.telegram_chat_id, 'text': part, 'parse_mode': 'Markdown'}
                    response = requests.post(url, data=payload, timeout=15)
                    response.raise_for_status()
                    if not response.json().get("ok"): raise Exception(f"Telegram API error: {response.json().get('description', 'Unknown')}")
                    if len(text_parts) > 1: time.sleep(0.5)
                lib_logger.info(f"Sent Telegram message (length {len(text)})")

            elif message_type == "document":
                filepath = kwargs.get("filepath")
                caption = kwargs.get("caption", "")
                if not filepath or not os.path.exists(filepath):
                    lib_logger.error(f"Telegram doc error: File not found '{filepath}'")
                    return
                url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendDocument"
                files = {'document': open(filepath, 'rb')}
                data = {'chat_id': self.config.telegram_chat_id, 'caption': caption[:1024]}
                try:
                    response = requests.post(url, files=files, data=data, timeout=90)
                    response.raise_for_status()
                    if not response.json().get("ok"): raise Exception(f"Telegram API error: {response.json().get('description', 'Unknown')}")
                    lib_logger.info(f"Sent Telegram document: {os.path.basename(filepath)}")
                finally:
                    files['document'].close()
        except Exception as e:
             lib_logger.error(f"Failed to send Telegram {message_type}: {e}", exc_info=False)

    # --- Keyword Generation ---
    def _generate_keywords_with_ai(self, num_keywords, custom_prompt_str):
        """Generates keywords using Google Gemini API."""
        self._check_cancel()
        if not self.config.gemini_api_key: raise ValueError("Google AI API Key not configured.")
        if not genai: raise RuntimeError("Google Generative AI library not available.")

        self._status_update(5, f"Generating {num_keywords} keywords via Google AI ({AI_MODEL_NAME})...")
        self._send_telegram(message_type="message", text=f"🚀 Starting AI keyword generation ({AI_MODEL_NAME})...")
        try:
            genai.configure(api_key=self.config.gemini_api_key)
            model = genai.GenerativeModel(AI_MODEL_NAME)
            prompt_to_use = custom_prompt_str or DEFAULT_AI_PROMPT_TEMPLATE.format(num_keywords=num_keywords)
            lib_logger.info(f"Using AI prompt: '{prompt_to_use[:100]}...'")
            response = model.generate_content(prompt_to_use)
            self._check_cancel()
            ai_keywords = [kw.strip() for kw in response.text.splitlines() if kw.strip() and len(kw) > 3]
            if ai_keywords:
                msg = f"AI generated {len(ai_keywords)} keywords."
                self._status_update(8, msg)
                self._send_telegram(message_type="message", text=f"✅ {msg}")
                return ai_keywords
            else: raise RuntimeError("AI returned empty or invalid keyword list.")
        except Exception as e:
            error_msg = f"Error during AI keyword generation: {e}"
            self._status_update(-1, error_msg)
            self._send_telegram(message_type="message", text=f"❌ AI keyword generation failed: {e}")
            raise RuntimeError(error_msg) from e

    def get_keywords(self):
        """Determines the list of keywords to use based on config."""
        self._status_update(2, "Determining keywords...")
        keywords = []
        source_msg = ""
        try:
            if self.config.keyword_source == 'ai':
                source_msg = "using Google AI"
                if not self.config.gemini_api_key: raise ValueError("Google AI API Key required but not provided.")
                keywords = self._generate_keywords_with_ai(NUM_KEYWORDS_TO_GENERATE, self.config.ai_prompt)
            elif self.config.keyword_source == 'list' and self.config.keywords_list:
                 source_msg = f"from provided list ({len(self.config.keywords_list)} keywords)"
                 keywords = self.config.keywords_list
            else: # Default
                source_msg = f"using default list ({len(DEFAULT_KEYWORDS)} keywords)"
                keywords = DEFAULT_KEYWORDS
            if not keywords: raise ValueError("No keywords found or generated.")

            self._status_update(10, f"Using {len(keywords)} keywords ({source_msg}).")
            try: # Save keywords used
                os.makedirs(os.path.dirname(self.config.keywords_file), exist_ok=True)
                with open(self.config.keywords_file, 'w', encoding='utf-8') as f:
                    json.dump(keywords, f, indent=2)
                self.files_saved.append(self.config.keywords_file)
                self._status_update(-1, f"Saved keywords used to {os.path.basename(self.config.keywords_file)}")
            except Exception as e: self._status_update(-1, f"Warning: Failed to save keywords file: {e}")
            return keywords
        except Exception as e:
             self._status_update(10, f"Error getting keywords: {e}")
             raise RuntimeError(f"Failed to get keywords: {e}") from e

    # --- Search Engine Functions ---
    def _search_ddg(self, keyword, max_results_target):
        """Searches DuckDuckGo using the library."""
        self._check_cancel()
        if not DDG_AVAILABLE:
            self._status_update(-1,"DDG library not available, cannot search.")
            return set()
        domains = set()
        lib_logger.debug(f"DDG: Searching '{keyword}' (target: {max_results_target})...")
        try:
            with DDGS(headers=self._get_random_headers(), timeout=REQUEST_TIMEOUT) as ddgs:
                results_iterator = ddgs.text(keyword, max_results=max_results_target)
                count = 0
                for r in results_iterator:
                    self._check_cancel()
                    if self.is_cancelled: break
                    if count >= max_results_target * 2 and max_results_target > 10: break
                    if isinstance(r, dict) and 'href' in r:
                        try:
                            parsed = urlparse(r['href'])
                            domain = parsed.netloc.lower().replace('www.', '')
                            if '.' in domain and 3 < len(domain) < 100 and 'duckduckgo.com' not in domain:
                                domains.add(domain)
                                count += 1
                        except Exception: continue
            lib_logger.debug(f"DDG: Found {len(domains)} unique domains for '{keyword}'.")
            return domains
        except ScraperCancelledError: raise
        except Exception as e:
            lib_logger.warning(f"DDG Error searching '{keyword}': {type(e).__name__} - {e}")
            return set()

    # --- Core Logic Steps ---
    def _collect_domains_master(self, keywords):
        """Collects domains using enabled search engines."""
        self._check_cancel()
        if not DDG_AVAILABLE:
            self._status_update(10, "DDG search library not available. Skipping.")
            return []

        target_total = self.config.target_domains
        start_prog, end_prog = 10, 40
        self._status_update(start_prog, f"Phase 1: Collecting domains (Target: {target_total})...")
        self._send_telegram(message_type="message", text=f"🔍 Collecting domains (~{target_total})...")

        domains_coll = set()
        kw_proc, dom_proc = 0, 0
        target_per = max(20, (target_total + len(keywords) - 1) // len(keywords) * 2)

        # No TQDM here, rely on status_callback
        for i, keyword in enumerate(keywords):
            self._check_cancel()
            if len(domains_coll) >= target_total:
                self._status_update(-1, "Target domain count reached.")
                break
            prog = start_prog + int((i / len(keywords)) * (end_prog - start_prog))
            self._status_update(prog, f"Searching kw {i+1}/{len(keywords)}: '{keyword[:40]}'...")

            remain = target_total - len(domains_coll)
            kw_target = max(10, min(target_per, remain + 20))
            try: new_dom = self._search_ddg(keyword, kw_target)
            except ScraperCancelledError: raise
            except Exception as e:
                 self._status_update(-1, f"Error searching '{keyword}', skipping: {e}")
                 continue

            dom_proc += len(new_dom)
            added_now = 0
            for domain in new_dom:
                if domain not in domains_coll:
                    domains_coll.add(domain)
                    added_now += 1
                    prog_o = start_prog + int((len(domains_coll)/target_total)*(end_prog-start_prog))
                    prog_o = min(end_prog, prog_o)
                    if len(domains_coll) % 50 == 0 or added_now == 1: # Update less often
                         self._status_update(prog_o, f"Domains found: {len(domains_coll)}/{target_total}")
                    if len(domains_coll) >= target_total: break
            self._status_update(-1, f"Keyword '{keyword[:40]}' added {added_now} new domains.")
            kw_proc += 1
            if len(domains_coll) < target_total:
                 time.sleep(random.uniform(REQUEST_DELAY_SEARCH * 0.5, REQUEST_DELAY_SEARCH * 1.0)) # Shorter delay maybe

        self.all_domains_found = domains_coll
        final_prog = start_prog + int((len(domains_coll) / target_total if target_total > 0 else 1) * (end_prog - start_prog))
        final_prog = min(end_prog, max(start_prog, final_prog))
        msg = f"Domain collection complete. Found {len(self.all_domains_found)} unique domains from {kw_proc} keywords."
        self._status_update(final_prog, msg)
        self._send_telegram(message_type="message", text=f"✅ {msg}")
        return list(self.all_domains_found)

    def _is_dealer_center(self, domain):
        """Checks a single domain for Dealer Center indicators."""
        self._check_cancel()
        urls = [f"https://{domain}", f"http://{domain}"]
        is_pos = False
        for url in urls:
            if self.is_cancelled: return None
            resp = self._safe_request(url)
            if resp:
                try:
                    hdrs = str(resp.headers).lower()
                    if any(re.search(p, hdrs, re.I) for p in DEALER_CENTER_INDICATORS): is_pos=True; break
                    f_dom = urlparse(resp.url).netloc.lower().replace('www.','')
                    if 'dealercenter' in f_dom: is_pos=True; break
                    cont = resp.content[:128*1024].decode('utf-8','ignore').lower()
                    if any(re.search(p, cont, re.I) for p in DEALER_CENTER_INDICATORS): is_pos=True; break
                except ScraperCancelledError: raise
                except Exception: pass
                break # Stop if one URL worked
        return domain if is_pos else None

    def _verify_dealer_center_sites(self, domains_to_check):
        """Verifies domains using threading."""
        self._check_cancel()
        if not domains_to_check:
            self._status_update(40, "No domains to verify.")
            return [], []

        start_prog, end_prog = 40, 70
        total_doms = len(domains_to_check)
        self._status_update(start_prog, f"Phase 2: Verifying {total_doms} domains...")
        self._send_telegram(message_type="message", text=f"🕵️ Verifying {total_doms} domains...")

        valid, invalid = [], []
        processed = 0

        # No TQDM here
        with ThreadPoolExecutor(max_workers=self.config.max_threads, thread_name_prefix="VerifyWorker") as executor:
            try:
                future_map = {executor.submit(self._is_dealer_center, d): d for d in domains_to_check}
                for future in as_completed(future_map):
                    self._check_cancel()
                    domain = future_map[future]
                    try:
                        result = future.result()
                        if result: valid.append(result)
                        else: invalid.append(domain)
                    except (CancelledError, ScraperCancelledError):
                        self._status_update(-1, "Verification cancelled.")
                        raise ScraperCancelledError("Verification cancelled.")
                    except Exception as exc:
                         lib_logger.warning(f"Error verifying {domain}: {exc}", exc_info=False)
                         invalid.append(domain)
                    finally:
                        processed += 1
                        if processed % 100 == 0 or processed == total_doms:
                             prog = start_prog + int((processed / total_doms) * (end_prog - start_prog))
                             self._status_update(prog, f"Verifying: {processed}/{total_doms} processed.")
            except ScraperCancelledError as e: raise e # Propagate
            except Exception as e:
                 self._status_update(-1, f"Unexpected error during verification: {e}")
                 raise # Re-raise

        self.valid_dealer_sites = valid
        self.invalid_sites = invalid
        self._save_domain_list(self.config.valid_domains_file, valid)
        self._save_domain_list(self.config.invalid_domains_file, invalid)

        msg = f"Verification complete. Found {len(valid)} potential DC sites, {len(invalid)} others."
        self._status_update(end_prog, msg)
        self._send_telegram(message_type="message", text=f"✅ {msg}")
        return valid, invalid

    def _save_domain_list(self, filepath, domain_list):
        """Saves a list of domains to a file."""
        if not filepath: return
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                for domain in domain_list: f.write(domain + '\n')
            self._status_update(-1, f"Saved {len(domain_list)} domains to {os.path.basename(filepath)}")
            self.files_saved.append(filepath)
            if domain_list:
                 time.sleep(0.5)
                 self._send_telegram(message_type="document", filepath=filepath, caption=f"Ayzen: {os.path.basename(filepath)} ({len(domain_list)})")
        except Exception as e:
            self._status_update(-1, f"Error saving domain list {os.path.basename(filepath)}: {e}")
            lib_logger.error(f"Failed to save domain list to {filepath}: {e}", exc_info=True)

    # --- Contact Extraction Logic ---
    def _extract_contacts_for_domain(self, domain):
        """Extracts contact info for a single domain."""
        self._check_cancel()
        start_time = datetime.now().isoformat()
        info = {'domain':domain,'url_checked':None,'emails':set(),'phones':set(),'address':None,'status':'Pending','timestamp':start_time}
        urls = [f"https://{domain}/contact", f"https://{domain}/contact-us", f"https://{domain}/about", f"https://{domain}/about-us", f"https://{domain}", f"http://{domain}"]
        html, text = "", ""
        final_url, soup = None, None
        https_ok = False

        for url in urls:
            if url.startswith('http://') and https_ok: continue
            if self.is_cancelled: break
            resp = self._safe_request(url)
            if url.startswith('https://'): https_ok = True
            if resp:
                try:
                    html = resp.content.decode('utf-8','ignore')
                    soup = BeautifulSoup(html,'html.parser')
                    body = soup.find('body')
                    text = body.get_text(' ',strip=True) if body else soup.get_text(' ',strip=True)
                    final_url = resp.url
                    info['url_checked'] = final_url
                    prio = any(s in url for s in ['/contact','/about'])
                    home = url.endswith(domain) or url.endswith(domain+'/')
                    if soup and (prio or (home and url.startswith('https://'))): break
                except ScraperCancelledError: raise
                except Exception as e: lib_logger.debug(f"Parse error {url}: {e}"); soup,text="",""

        if self.is_cancelled: info['status']='Cancelled'; info['emails']=sorted(list(info['emails'])); info['phones']=sorted(list(info['phones'])); return info
        if not soup and not text: info['status']='No Content'; info['emails']=[]; info['phones']=[]; return info

        try: # Extraction
            # Emails
            pattern = r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
            found = set(re.findall(pattern, html)).union(set(re.findall(pattern, text)))
            if soup:
                for link in soup.find_all('a',href=lambda h: h and h.startswith('mailto:')):
                    part=link['href'][7:].split('?')[0].strip()
                    if re.fullmatch(pattern, part): found.add(part)
            ignore = ('.png','.jpg','.gif','.webp','.svg','.css','.js','.woff','.ttf','.pdf','.zip')
            for e in found:
                low=e.lower()
                if not low.endswith(ignore):
                    parts=low.split('@')
                    if len(parts)==2 and '.' in parts[1] and len(parts[1])>3 and len(e)<100: info['emails'].add(low)
            # Phones
            phones = set()
            if text:
                try:
                    for m in phonenumbers.PhoneNumberMatcher(text,"US"):
                        num=m.number; nat=phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL); e164=phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
                        if 10<=len(re.sub(r'\D','',e164))<=15: phones.add(nat)
                except Exception: phones.update(re.findall(r'\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',text))
            if soup:
                for link in soup.find_all('a',href=lambda h: h and h.startswith('tel:')):
                    tel=link['href'][4:].strip(); clean=re.sub(r'[^\d+]','',tel)
                    if 10<=len(clean.replace('+','',1))<=15:
                        try:
                            p=phonenumbers.parse(clean,"US")
                            if phonenumbers.is_possible_number(p): phones.add(phonenumbers.format_number(p,phonenumbers.PhoneNumberFormat.NATIONAL))
                        except Exception: pass
            info['phones'].update(phones)
            # Address
            addr, best = [], None
            if soup:
                sels = ['address','[itemprop*="address"]','[class*="address"]','[class*="location"]','footer']
                proc = set()
                for sel in sels:
                    try:
                        for tag in soup.select(sel):
                            t=' '.join(tag.stripped_strings); c=re.sub(r'\s{2,}',' ',t).strip()
                            if 15<len(c)<300 and c not in proc:
                                proc.add(c)
                                if re.search(r'\d',c) and re.search(r'(?i)\b(st|ave|rd|dr|blvd|ct|hwy)\b',c) and re.search(r'\b\d{5}(?:-\d{4})?\b',c): addr.append(c)
                    except Exception: pass
                if addr: best=sorted(list(set(addr)),key=len,reverse=True)[0]
            info['address'] = best
            # Status
            info['status'] = 'Success' if info['emails'] or info['phones'] or info['address'] else 'No Contacts'
        except ScraperCancelledError: raise
        except Exception as e: lib_logger.warning(f"Extract Logic Error {domain}: {e}"); info['status']=f"Extract Error: {str(e)[:50]}"

        info['emails'] = sorted(list(info['emails'])); info['phones'] = sorted(list(info['phones']))
        return info


    def _extract_contacts_and_save(self, sites_to_scrape):
        """Extracts contacts and saves results incrementally."""
        self._check_cancel()
        if not sites_to_scrape:
            self._status_update(70, "No valid sites to extract contacts from.")
            return 0

        start_prog, end_prog = 70, 98
        total_sites = len(sites_to_scrape)
        self._status_update(start_prog, f"Phase 3: Extracting contacts from {total_sites} sites...")
        self._send_telegram(message_type="message", text=f"📝 Extracting contacts from {total_sites} sites...")

        extracted_c, error_c, processed_c = 0, 0, 0
        csv_hdrs = ['domain', 'emails', 'phones', 'address', 'url_checked', 'status', 'timestamp']
        json_fh, csv_fh, csv_w = None, None, None

        try: # Open files
            os.makedirs(os.path.dirname(self.config.contacts_json_file), exist_ok=True)
            os.makedirs(os.path.dirname(self.config.contacts_csv_file), exist_ok=True)
            json_fh = open(self.config.contacts_json_file, 'a', encoding='utf-8')
            exists = os.path.exists(self.config.contacts_csv_file)
            empty = not exists or os.path.getsize(self.config.contacts_csv_file)==0
            csv_fh = open(self.config.contacts_csv_file, 'a', newline='', encoding='utf-8')
            csv_w = csv.DictWriter(csv_fh, fieldnames=csv_hdrs, extrasaction='ignore')
            if empty: csv_w.writeheader()
            self.files_saved.extend([self.config.contacts_json_file, self.config.contacts_csv_file])
        except IOError as e:
             self._status_update(start_prog, f"CRITICAL ERROR opening output files: {e}")
             self._send_telegram(message_type="message", text=f"❌ CRITICAL ERROR opening output files: {e}")
             raise RuntimeError(f"Failed to open output files: {e}") from e

        # --- Threaded Extraction ---
        # No TQDM needed here
        try:
            with ThreadPoolExecutor(max_workers=self.config.max_threads, thread_name_prefix="ExtractWorker") as executor:
                future_map = {executor.submit(self._extract_contacts_for_domain, s): s for s in sites_to_scrape}
                for future in as_completed(future_map):
                    self._check_cancel()
                    site = future_map[future]
                    contact_data = None
                    try:
                        contact_data = future.result()
                        if contact_data:
                            try: json_fh.write(json.dumps(contact_data) + '\n')
                            except Exception as json_e: lib_logger.error(f"JSON write error {site}: {json_e}")
                            try:
                                csv_r = contact_data.copy()
                                csv_r['emails'] = ', '.join(csv_r.get('emails', []))
                                csv_r['phones'] = ', '.join(csv_r.get('phones', []))
                                for k in csv_hdrs: csv_r[k] = str(csv_r.get(k) or '')
                                csv_w.writerow({k: csv_r[k] for k in csv_hdrs if k in csv_r})
                            except Exception as csv_e: lib_logger.error(f"CSV write error {site}: {csv_e}")

                            if contact_data['status']=='Success': extracted_c+=1
                            elif contact_data['status']!='Cancelled': error_c+=1
                        else: error_c+=1
                    except (CancelledError, ScraperCancelledError):
                        self._status_update(-1, "Extraction cancelled.")
                        raise ScraperCancelledError("Extraction cancelled.")
                    except Exception as exc:
                        error_c+=1
                        lib_logger.warning(f"Extract exception {site}: {exc}", exc_info=False)
                    finally:
                        processed_c+=1
                        if processed_c % 100 == 0 or processed_c == total_sites:
                             prog = start_prog + int((processed_c / total_sites) * (end_prog - start_prog))
                             self._status_update(prog, f"Extracting: {processed_c}/{total_sites} processed.")
        except ScraperCancelledError as e: raise e
        except Exception as e:
            self._status_update(-1, f"Unexpected error during extraction: {e}")
            raise
        finally: # Ensure files are closed
            if json_fh: try: json_fh.flush(); json_fh.close() except: pass
            if csv_fh: try: csv_fh.flush(); csv_fh.close() except: pass

        self.extracted_contacts_count = extracted_c
        msg = f"Extraction complete. Contacts found for {extracted_c}. Errors/Skipped for {error_c}."
        self._status_update(end_prog, msg)
        self._send_telegram(message_type="message", text=f"✅ {msg}")
        if extracted_c > 0: # Send result files
             if self.config.contacts_json_file in self.files_saved:
                 time.sleep(0.5); self._send_telegram(message_type="document", filepath=self.config.contacts_json_file, caption=f"Ayzen: Final JSON Contacts ({extracted_c} sites)")
             if self.config.contacts_csv_file in self.files_saved:
                 time.sleep(0.5); self._send_telegram(message_type="document", filepath=self.config.contacts_csv_file, caption=f"Ayzen: Final CSV Contacts ({extracted_c} sites)")
        return extracted_c

    # --- Main Execution Method ---
    def run(self):
        """Runs the entire scraping process."""
        start_time = time.time()
        self.is_cancelled = False
        self.files_saved = []
        keywords = []

        # No signal handling needed when run as library by Flask
        self._status_update(0, f"Ayzen Scraper starting via backend for user: {self.config.user_name}...")
        self._send_telegram(message_type="message",
                            text=f"👋 Backend scraper started by: {self.config.user_name} | Target: {self.config.target_domains} | Keywords: {self.config.keyword_source} | Threads: {self.config.max_threads} | TG: {'On' if self.config.send_telegram else 'Off'}")

        final_msg = "Scraper finished unexpectedly."
        try:
            # Workflow Steps
            keywords = self.get_keywords() # 0-10%
            domains = self._collect_domains_master(keywords) # 10-40%
            if not domains:
                 final_msg="Finished: No domains collected."; self._status_update(100,final_msg)
                 self._send_telegram(message_type="message", text=f"🏁 {final_msg}"); return final_msg, self.files_saved
            valid, _ = self._verify_dealer_center_sites(domains) # 40-70%
            if not valid:
                 final_msg="Finished: No valid DC sites found."; self._status_update(100,final_msg)
                 self._send_telegram(message_type="message", text=f"🏁 {final_msg}"); return final_msg, self.files_saved
            self._extract_contacts_and_save(valid) # 70-98%

            # Final Report
            duration = time.time() - start_time
            summary = f"🏁 Run Finished!\nUser: {self.config.user_name}\nDur: {duration:.1f}s\nKw: {len(keywords)}\nDom: {len(self.all_domains_found)}\nValid: {len(self.valid_dealer_sites)}\nContacts: {self.extracted_contacts_count}"
            final_msg = f"Finished OK ({duration:.1f}s). Contacts: {self.extracted_contacts_count}."
            self._status_update(100, final_msg)
            self._send_telegram(message_type="message", text=summary)
            return final_msg, self.files_saved

        except ScraperCancelledError as e:
            duration = time.time() - start_time
            final_msg = f"🛑 Cancelled after {duration:.1f}s. Partial results saved."
            self._status_update(100, final_msg)
            self._send_telegram(message_type="message", text=final_msg)
            return final_msg, self.files_saved
        except Exception as e:
            duration = time.time() - start_time
            lib_logger.error(f"CRITICAL ERROR after {duration:.1f}s: {e}", exc_info=True)
            final_msg = f"❌ CRITICAL ERROR: {type(e).__name__} - {e}"
            self._status_update(100, final_msg)
            self._send_telegram(message_type="message", text=f"{final_msg}\nCheck backend logs!")
            # Re-raise the original exception for the backend wrapper to catch
            raise RuntimeError(f"Scraper run failed: {e}") from e

# --- END OF FILE backend/src/ayzzn_pro_library.py ---
