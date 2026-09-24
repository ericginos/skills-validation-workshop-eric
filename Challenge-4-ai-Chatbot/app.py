"""
Interactive AI Chatbot with Real-Time Google Search Grounding
Powered by Google Gemini 2.5 and Streamlit.
Optimized for deployment on Google Cloud Run.
"""

import os
import sys
import time
from typing import Dict, List, Optional, Tuple, Any

import streamlit as st
from google.genai import Client, types, errors

# ==============================================================================
# Page Configuration & Styling
# ==============================================================================
st.set_page_config(
    page_title="Gemini AI Chatbot - Real-Time Search Grounding",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom styling for UI aesthetics
st.markdown(
    """
    <style>
    /* Main container styling */
    .main-header {
        font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
        margin-bottom: 1.5rem;
    }
    .badge-grounding {
        background: linear-gradient(135deg, #4285F4 0%, #34A853 100%);
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        margin-left: 8px;
    }
    .badge-pill {
        display: inline-block;
        background-color: #f1f3f4;
        color: #3c4043;
        border: 1px solid #dadce0;
        border-radius: 16px;
        padding: 3px 10px;
        font-size: 0.78rem;
        margin-right: 6px;
        margin-bottom: 6px;
        font-family: monospace;
    }
    .source-box {
        background-color: #f8f9fa;
        border-left: 4px solid #1a73e8;
        padding: 10px 14px;
        border-radius: 4px;
        margin-top: 8px;
        font-size: 0.88rem;
    }
    /* Dark mode adjustments */
    @media (prefers-color-scheme: dark) {
        .badge-pill {
            background-color: #303134;
            color: #e8eaed;
            border-color: #5f6368;
        }
        .source-box {
            background-color: #202124;
            border-left-color: #8ab4f8;
            color: #e8eaed;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==============================================================================
# Authentication & Client Management
# ==============================================================================
def get_auth_context() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Discovers project, region, and API key from environment."""
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("PROJECT_ID")
    )
    region = (
        os.environ.get("GOOGLE_CLOUD_LOCATION")
        or os.environ.get("GCP_REGION")
        or "us-central1"
    )
    api_key = os.environ.get("GEMINI_API_KEY")
    return project, region, api_key


@st.cache_resource(show_spinner=False)
def initialize_gemini_client(
    api_key_override: Optional[str] = None,
    project_override: Optional[str] = None,
    region_override: Optional[str] = None,
) -> Tuple[Optional[Client], str, Optional[str]]:
    """
    Initializes and caches the Google GenAI Client with multi-mode fallback:
      1. Explicit or environment GEMINI_API_KEY (Developer API)
      2. Vertex AI using Google Cloud Application Default Credentials (ADC)
    Returns: (client, auth_mode_description, error_message)
    """
    env_project, env_region, env_api_key = get_auth_context()
    resolved_api_key = (api_key_override or env_api_key or "").strip()
    resolved_project = (project_override or env_project or "").strip()
    resolved_region = (region_override or env_region or "us-central1").strip()

    # 1. Try API Key authentication if present
    if resolved_api_key:
        try:
            client = Client(api_key=resolved_api_key)
            return client, "Gemini API Key Authentication", None
        except Exception as e:
            return None, "API Key Error", f"Failed to initialize Gemini with API Key: {e}"

    # 2. Try Vertex AI Application Default Credentials (ADC)
    if resolved_project:
        try:
            client = Client(
                vertexai=True,
                project=resolved_project,
                location=resolved_region,
            )
            mode_desc = f"Vertex AI (Project: {resolved_project}, Region: {resolved_region})"
            return client, mode_desc, None
        except Exception as e:
            return None, "Vertex AI ADC Error", f"Failed to initialize Vertex AI client: {e}"

    # 3. Fallback attempt to default Client()
    try:
        client = Client()
        return client, "Standard Default Credentials", None
    except Exception as e:
        err_msg = (
            "No authentication credentials found. "
            "Please provide a GEMINI_API_KEY or configure Vertex AI default credentials."
        )
        return None, "Unauthenticated", err_msg


# ==============================================================================
# Grounding Data Extraction Helper
# ==============================================================================
def extract_grounding_info(grounding_metadata: Any) -> Dict[str, Any]:
    """
    Extracts web search queries and cited web sources from Gemini GroundingMetadata.
    """
    sources: List[Dict[str, str]] = []
    queries: List[str] = []

    if not grounding_metadata:
        return {"sources": sources, "queries": queries}

    # Extract web search queries
    if getattr(grounding_metadata, "web_search_queries", None):
        queries = [str(q) for q in grounding_metadata.web_search_queries if q]

    # Extract grounding chunks (sources cited by the model)
    chunks = getattr(grounding_metadata, "grounding_chunks", None)
    if chunks:
        seen_urls = set()
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                uri = str(web.uri).strip()
                if uri and uri not in seen_urls:
                    seen_urls.add(uri)
                    title = getattr(web, "title", None) or uri
                    domain = getattr(web, "domain", None) or ""
                    sources.append({
                        "title": str(title).strip(),
                        "url": uri,
                        "domain": str(domain).strip(),
                    })

    return {"sources": sources, "queries": queries}


# ==============================================================================
# Chat History Conversion
# ==============================================================================
def build_genai_history(messages: List[Dict[str, Any]]) -> List[types.Content]:
    """
    Converts session state message history into google-genai Content types
    for multi-turn conversational context.
    """
    history: List[types.Content] = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        content_text = msg.get("content", "")
        if content_text:
            history.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=content_text)],
                )
            )
    return history


# ==============================================================================
# Sidebar Controls & Settings
# ==============================================================================
def render_sidebar() -> Tuple[Optional[Client], Dict[str, Any]]:
    """Renders the Streamlit sidebar controls and returns client and config."""
    with st.sidebar:
        st.title("⚙️ Configuration")

        # LLM Engine & Model
        st.subheader("Model Settings")
        model_options = [
            "gemini-2.5-flash",
            "gemini-1.5-flash",
            "gemini-2.5-pro",
        ]
        selected_model = st.selectbox(
            "Select Gemini Model",
            options=model_options,
            index=0,
            help="gemini-2.5-flash is optimized for speed, reasoning, and real-time grounding.",
        )

        # Grounding Feature Controls
        st.subheader("Search Grounding")
        enable_grounding = st.toggle(
            "Enable Google Search Grounding",
            value=True,
            help="Empowers Gemini to perform live Google searches to ground answers in verified real-time facts.",
        )

        show_citations = st.checkbox(
            "Display Grounding Sources & Queries",
            value=True,
            disabled=not enable_grounding,
            help="Displays source URLs, domains, and queries executed by the model.",
        )

        streaming_mode = st.toggle(
            "Streaming Response Mode",
            value=True,
            help="Stream tokens in real-time as they are generated by the model.",
        )

        # Model Hyperparameters
        with st.expander("Hyperparameters", expanded=False):
            temperature = st.slider(
                "Temperature",
                min_value=0.0,
                max_value=2.0,
                value=0.7,
                step=0.05,
                help="Higher values yield more creative responses; lower values are more deterministic.",
            )
            top_p = st.slider(
                "Top-P",
                min_value=0.0,
                max_value=1.0,
                value=0.95,
                step=0.05,
                help="Nucleus sampling threshold.",
            )
            system_instruction = st.text_area(
                "System Instruction",
                value="You are a helpful, accurate, and articulate AI assistant. When search grounding is enabled, summarize up-to-date facts clearly and reference verified details.",
                height=100,
                help="Steers the behavior, persona, and tone of the model.",
            )

        # Authentication Settings
        env_project, env_region, env_key = get_auth_context()
        with st.expander("Authentication & Cloud Context", expanded=False):
            api_key_input = st.text_input(
                "Gemini API Key (Optional Override)",
                type="password",
                value="",
                placeholder="AIzaSy...",
                help="Provide an API key or leave blank to use Vertex AI default credentials.",
            )
            project_input = st.text_input(
                "GCP Project ID",
                value=env_project or "",
                placeholder="my-gcp-project-id",
            )
            region_input = st.text_input(
                "GCP Region",
                value=env_region or "us-central1",
            )

        # Initialize Client
        client, auth_mode, auth_error = initialize_gemini_client(
            api_key_override=api_key_input if api_key_input else None,
            project_override=project_input if project_input else None,
            region_override=region_input if region_input else None,
        )

        # Connection Status Display
        st.divider()
        if client:
            st.success(f"🟢 Connected: {auth_mode}")
        else:
            st.error(f"🔴 Authentication Failed: {auth_error}")

        # Session Actions
        st.subheader("Session Management")
        if st.button("🗑️ Clear Conversation", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.caption(
            f"Stored turns: {len(st.session_state.get('messages', [])) // 2} exchanges"
        )

    config_dict = {
        "model": selected_model,
        "enable_grounding": enable_grounding,
        "show_citations": show_citations,
        "streaming_mode": streaming_mode,
        "temperature": temperature,
        "top_p": top_p,
        "system_instruction": system_instruction.strip(),
    }
    return client, config_dict


# ==============================================================================
# Main Application Logic
# ==============================================================================
def main():
    # Initialize session state for messages
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Render sidebar and acquire client + parameters
    client, config = render_sidebar()

    # Main Page Title & Header
    st.markdown(
        """
        <div class="main-header">
            <h2>🤖 Gemini AI Chatbot <span class="badge-grounding">Google Search Grounded</span></h2>
            <p style="color: #5f6368; font-size: 1.05rem; margin-top: -6px;">
                Intelligent conversational AI powered by <b>Google Gemini</b> with real-time web fact grounding and verifiable citations.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # If client initialization failed, display actionable instructions
    if not client:
        st.error(
            "⚠️ **Gemini Client is not connected.** Please configure either:\n"
            "1. A valid `GEMINI_API_KEY` in environment variables or the sidebar.\n"
            "2. Google Cloud credentials with Vertex AI enabled in project (`gcloud auth application-default login`)."
        )
        return

    # Render starter prompt suggestions if conversation is empty
    if len(st.session_state.messages) == 0:
        st.markdown("### 💡 Try an Example Real-Time Question")
        sample_queries = [
            "What are the top headlines and breaking tech news today?",
            "What is the current stock price and recent performance of Alphabet (GOOGL)?",
            "What are the latest discoveries or updates from the James Webb Space Telescope?",
            "Summarize the latest developments in quantum computing this month.",
        ]
        cols = st.columns(2)
        for idx, prompt_sample in enumerate(sample_queries):
            target_col = cols[idx % 2]
            if target_col.button(f"🔍 {prompt_sample}", key=f"sample_{idx}", use_container_width=True):
                st.session_state.active_prompt = prompt_sample
                st.rerun()

    # Render existing conversation history
    for msg in st.session_state.messages:
        role = msg["role"]
        with st.chat_message(role):
            st.markdown(msg["content"])

            # If assistant message has queries, display query pills
            queries = msg.get("queries", [])
            if queries and config["show_citations"]:
                query_pills = " ".join([f"<span class='badge-pill'>🔍 {q}</span>" for q in queries])
                st.markdown(query_pills, unsafe_allow_html=True)

            # If assistant message has grounded sources, display source expander
            sources = msg.get("sources", [])
            if sources and config["show_citations"]:
                with st.expander(f"🌐 Grounded Sources ({len(sources)} cited)", expanded=False):
                    for s_idx, source in enumerate(sources, start=1):
                        title = source.get("title", "Web Source")
                        url = source.get("url", "#")
                        domain = source.get("domain", "")
                        domain_tag = f" &nbsp;`{domain}`" if domain else ""
                        st.markdown(f"{s_idx}. [{title}]({url}){domain_tag}")

    # Capture chat input (either from bottom input box or sample buttons)
    chat_input = st.chat_input("Ask a question, analyze current events, or request real-time information...")
    user_prompt = None

    if chat_input:
        user_prompt = chat_input
    elif "active_prompt" in st.session_state and st.session_state.active_prompt:
        user_prompt = st.session_state.pop("active_prompt")

    # Process new user turn
    if user_prompt:
        # Display user message in UI
        st.chat_message("user").markdown(user_prompt)

        # Build GenAI tool configuration
        tools = []
        if config["enable_grounding"]:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        genai_config = types.GenerateContentConfig(
            temperature=config["temperature"],
            top_p=config["top_p"],
            system_instruction=config["system_instruction"] or None,
            tools=tools if tools else None,
        )

        # Reconstruct multi-turn history from previous messages
        history = build_genai_history(st.session_state.messages)

        # Append current user prompt to session state
        st.session_state.messages.append({
            "role": "user",
            "content": user_prompt,
            "timestamp": time.strftime("%H:%M:%S"),
        })

        # Generate Assistant Response
        with st.chat_message("assistant"):
            full_response_text = ""
            grounding_meta = None

            try:
                # Initialize multi-turn chat session with Gemini
                chat = client.chats.create(
                    model=config["model"],
                    config=genai_config,
                    history=history,
                )

                if config["streaming_mode"]:
                    # Real-time streaming response
                    response_stream = chat.send_message_stream(user_prompt)

                    def text_generator():
                        nonlocal full_response_text, grounding_meta
                        for chunk in response_stream:
                            if chunk.text:
                                full_response_text += chunk.text
                                yield chunk.text
                            # Capture grounding metadata from stream candidate chunks
                            if chunk.candidates and chunk.candidates[0].grounding_metadata:
                                grounding_meta = chunk.candidates[0].grounding_metadata

                    full_response_text = st.write_stream(text_generator())
                else:
                    # Unary response with progress spinner
                    with st.spinner("Searching Google and formulating response..."):
                        response = chat.send_message(user_prompt)
                        full_response_text = response.text or ""
                        if response.candidates and response.candidates[0].grounding_metadata:
                            grounding_meta = response.candidates[0].grounding_metadata
                        st.markdown(full_response_text)

                # Extract grounding citations and queries
                grounding_info = extract_grounding_info(grounding_meta)
                sources = grounding_info["sources"]
                queries = grounding_info["queries"]

                # Render search queries and sources if present
                if queries and config["show_citations"]:
                    query_pills = " ".join([f"<span class='badge-pill'>🔍 {q}</span>" for q in queries])
                    st.markdown(query_pills, unsafe_allow_html=True)

                if sources and config["show_citations"]:
                    with st.expander(f"🌐 Grounded Sources ({len(sources)} cited)", expanded=False):
                        for s_idx, source in enumerate(sources, start=1):
                            title = source.get("title", "Web Source")
                            url = source.get("url", "#")
                            domain = source.get("domain", "")
                            domain_tag = f" &nbsp;`{domain}`" if domain else ""
                            st.markdown(f"{s_idx}. [{title}]({url}){domain_tag}")

                # Save assistant response to conversation history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": full_response_text,
                    "sources": sources,
                    "queries": queries,
                    "timestamp": time.strftime("%H:%M:%S"),
                })

            except errors.APIError as api_err:
                error_details = f"Gemini API Error: {api_err.message} (Code: {api_err.code})"
                st.error(error_details)
            except Exception as e:
                st.error(f"An unexpected error occurred while generating response: {e}")


if __name__ == "__main__":
    main()
