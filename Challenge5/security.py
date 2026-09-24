"""Security, Model Armor, DLP, and Safety Settings Module for Alaska Department of Snow (ADS).

This module provides enterprise-grade data governance:
1. Model Armor Integration (google.cloud.modelarmor_v1) with regional endpoints.
   - sanitize_user_input: Validates prompts against prompt injection / jailbreak / malicious URIs.
   - sanitize_model_output: Inspects model responses before display or storage.
2. Sensitive Data Protection (DLP & Regex):
   - Scans and masks PII/SPII (Driver IDs, phone numbers, GPS coordinates, emails, names).
3. Gemini Safety Settings:
   - Configures explicit safety thresholds (Hate Speech, Dangerous Content, Harassment, Sexually Explicit)
     using google.genai.types.SafetySetting in a reusable GenerateContentConfig.
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import dlp_v2, modelarmor_v1
from google.genai import types

from logger import get_logger

logger = get_logger("ads_security")

# Configuration defaults
DEFAULT_PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-02-1c7c179a8d73")
DEFAULT_LOCATION = os.environ.get("MODEL_ARMOR_LOCATION") or os.environ.get("LOCATION", "us-central1")
DEFAULT_TEMPLATE_ID = os.environ.get("MODEL_ARMOR_TEMPLATE_ID") or os.environ.get("TEMPLATE_ID", "ads-operational-template")


# =====================================================================
# 1. MODEL ARMOR INTEGRATION
# =====================================================================

def get_model_armor_client(location: Optional[str] = None) -> modelarmor_v1.ModelArmorClient:
    """Creates a Model Armor client configured with the regional endpoint.

    Args:
        location: Regional location (e.g. 'us-central1').

    Returns:
        modelarmor_v1.ModelArmorClient
    """
    loc = location or DEFAULT_LOCATION
    regional_endpoint = f"modelarmor.{loc}.rep.googleapis.com"
    logger.debug(f"Initializing ModelArmorClient with endpoint: {regional_endpoint}")
    return modelarmor_v1.ModelArmorClient(
        client_options=ClientOptions(api_endpoint=regional_endpoint)
    )


def build_template_path(
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    template_id: Optional[str] = None,
) -> str:
    """Builds the fully-qualified resource name for a Model Armor template."""
    proj = project_id or DEFAULT_PROJECT_ID
    loc = location or DEFAULT_LOCATION
    tmpl = template_id or DEFAULT_TEMPLATE_ID

    if tmpl.startswith("projects/"):
        return tmpl
    return f"projects/{proj}/locations/{loc}/templates/{tmpl}"


def ensure_model_armor_template(
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    template_id: Optional[str] = None,
) -> str:
    """Ensures that the required Model Armor template exists, creating it if needed.

    Args:
        project_id: GCP project ID.
        location: Regional location.
        template_id: Template ID name.

    Returns:
        Full template resource name.
    """
    proj = project_id or DEFAULT_PROJECT_ID
    loc = location or DEFAULT_LOCATION
    tmpl = template_id or DEFAULT_TEMPLATE_ID
    full_name = build_template_path(proj, loc, tmpl)

    client = get_model_armor_client(loc)
    try:
        client.get_template(name=full_name)
        logger.debug(f"Model Armor template exists: {full_name}")
        return full_name
    except NotFound:
        logger.info(f"Creating Model Armor template '{tmpl}' at '{loc}'")
        parent = f"projects/{proj}/locations/{loc}"
        template_obj = modelarmor_v1.Template(
            filter_config=modelarmor_v1.FilterConfig(
                pi_and_jailbreak_filter_settings=modelarmor_v1.PiAndJailbreakFilterSettings(
                    filter_enforcement=modelarmor_v1.PiAndJailbreakFilterSettings.PiAndJailbreakFilterEnforcement.ENABLED
                ),
                malicious_uri_filter_settings=modelarmor_v1.MaliciousUriFilterSettings(
                    filter_enforcement=modelarmor_v1.MaliciousUriFilterSettings.MaliciousUriFilterEnforcement.ENABLED
                ),
            )
        )
        created = client.create_template(
            parent=parent,
            template_id=tmpl,
            template=template_obj,
        )
        logger.info(f"Model Armor template successfully created: {created.name}")
        return created.name
    except Exception as e:
        logger.warning(f"Could not verify/create template ({e}). Returning template name.")
        return full_name


def sanitize_user_input(
    prompt_text: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validates incoming user prompts against the specified Model Armor template.

    Args:
        prompt_text: Prompt or query string to inspect.
        project_id: GCP Project ID.
        location: Model Armor regional location.
        template_id: Model Armor template ID.

    Returns:
        Dictionary containing sanitization outcome:
        - 'is_safe': bool (True if no filter matches/violations)
        - 'sanitization_status': str ('PASSED' or 'BLOCKED')
        - 'filter_match_state': str ('NO_MATCH_FOUND', 'MATCH_FOUND', etc.)
        - 'violations': List[str] of matched filter names
        - 'filter_results': Dict of detailed filter inspection results
        - 'template_name': Full resource path of the template used
    """
    loc = location or DEFAULT_LOCATION
    template_name = build_template_path(project_id, loc, template_id)
    client = get_model_armor_client(loc)

    logger.info(
        "Sanitizing user input via Model Armor",
        extra={"template_name": template_name, "prompt_length": len(prompt_text)},
    )

    request = modelarmor_v1.SanitizeUserPromptRequest(
        name=template_name,
        user_prompt_data=modelarmor_v1.DataItem(text=prompt_text),
    )

    try:
        response = client.sanitize_user_prompt(request=request)
        result = response.sanitization_result

        # Match state enum conversion
        match_state_name = modelarmor_v1.FilterMatchState(result.filter_match_state).name
        is_safe = (result.filter_match_state == modelarmor_v1.FilterMatchState.NO_MATCH_FOUND)

        violations = []
        filter_results_dict = {}
        for filter_name, filter_data in result.filter_results.items():
            # FilterResult contains specific sub-result (e.g. pi_and_jailbreak_filter_result)
            sub_res = None
            for sub_field in [
                f"{filter_name}_filter_result",
                f"{filter_name}_result",
                "pi_and_jailbreak_filter_result",
                "malicious_uri_filter_result",
                "csam_filter_filter_result",
                "rai_filter_result",
                "sdp_filter_result",
                "virus_scan_filter_result",
            ]:
                if hasattr(filter_data, sub_field) and getattr(filter_data, sub_field):
                    sub_res = getattr(filter_data, sub_field)
                    break

            state_val = getattr(sub_res, "match_state", getattr(filter_data, "match_state", None))
            state_name = modelarmor_v1.FilterMatchState(state_val).name if state_val is not None else "UNKNOWN"
            conf_val = getattr(sub_res, "confidence_level", getattr(filter_data, "confidence_level", None))
            conf_name = str(conf_val.name) if hasattr(conf_val, "name") else str(conf_val) if conf_val else None

            filter_results_dict[filter_name] = {
                "match_state": state_name,
                "confidence_level": conf_name,
            }
            if state_val == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                violations.append(filter_name)

        status = "PASSED" if is_safe else "BLOCKED"

        logger.info(
            f"User input sanitization status: {status}",
            extra={
                "sanitization_status": status,
                "is_safe": is_safe,
                "violations": violations,
                "filter_match_state": match_state_name,
            },
        )

        return {
            "is_safe": is_safe,
            "sanitization_status": status,
            "filter_match_state": match_state_name,
            "violations": violations,
            "filter_results": filter_results_dict,
            "template_name": template_name,
            "prompt_text": prompt_text,
        }

    except Exception as e:
        logger.error(f"Error sanitizing user input with Model Armor: {e}")
        # Fail closed for security in enterprise environment
        return {
            "is_safe": False,
            "sanitization_status": "ERROR",
            "filter_match_state": "ERROR",
            "violations": [str(e)],
            "filter_results": {},
            "template_name": template_name,
            "prompt_text": prompt_text,
        }


def sanitize_model_output(
    response_text: str,
    user_prompt: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspects generated model responses before displaying or storing them.

    Args:
        response_text: Text generated by the LLM.
        user_prompt: Original user prompt that produced this response (optional).
        project_id: GCP Project ID.
        location: Model Armor regional location.
        template_id: Model Armor template ID.

    Returns:
        Dictionary containing sanitization outcome:
        - 'is_safe': bool (True if no filter matches/violations)
        - 'sanitization_status': str ('PASSED', 'FLAGGED', or 'BLOCKED')
        - 'filter_match_state': str
        - 'violations': List[str]
        - 'filter_results': Dict
        - 'template_name': Full resource path of the template used
    """
    loc = location or DEFAULT_LOCATION
    template_name = build_template_path(project_id, loc, template_id)
    client = get_model_armor_client(loc)

    logger.info(
        "Sanitizing model output via Model Armor",
        extra={"template_name": template_name, "response_length": len(response_text)},
    )

    request = modelarmor_v1.SanitizeModelResponseRequest(
        name=template_name,
        model_response_data=modelarmor_v1.DataItem(text=response_text),
        user_prompt=user_prompt or "",
    )

    try:
        response = client.sanitize_model_response(request=request)
        result = response.sanitization_result

        match_state_name = modelarmor_v1.FilterMatchState(result.filter_match_state).name
        is_safe = (result.filter_match_state == modelarmor_v1.FilterMatchState.NO_MATCH_FOUND)

        violations = []
        filter_results_dict = {}
        for filter_name, filter_data in result.filter_results.items():
            sub_res = None
            for sub_field in [
                f"{filter_name}_filter_result",
                f"{filter_name}_result",
                "pi_and_jailbreak_filter_result",
                "malicious_uri_filter_result",
                "csam_filter_filter_result",
                "rai_filter_result",
                "sdp_filter_result",
                "virus_scan_filter_result",
            ]:
                if hasattr(filter_data, sub_field) and getattr(filter_data, sub_field):
                    sub_res = getattr(filter_data, sub_field)
                    break

            state_val = getattr(sub_res, "match_state", getattr(filter_data, "match_state", None))
            state_name = modelarmor_v1.FilterMatchState(state_val).name if state_val is not None else "UNKNOWN"
            conf_val = getattr(sub_res, "confidence_level", getattr(filter_data, "confidence_level", None))
            conf_name = str(conf_val.name) if hasattr(conf_val, "name") else str(conf_val) if conf_val else None

            filter_results_dict[filter_name] = {
                "match_state": state_name,
                "confidence_level": conf_name,
            }
            if state_val == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                violations.append(filter_name)

        status = "PASSED" if is_safe else "BLOCKED"

        logger.info(
            f"Model output sanitization status: {status}",
            extra={
                "sanitization_status": status,
                "is_safe": is_safe,
                "violations": violations,
                "filter_match_state": match_state_name,
            },
        )

        return {
            "is_safe": is_safe,
            "sanitization_status": status,
            "filter_match_state": match_state_name,
            "violations": violations,
            "filter_results": filter_results_dict,
            "template_name": template_name,
            "response_text": response_text,
        }

    except Exception as e:
        logger.error(f"Error sanitizing model output with Model Armor: {e}")
        return {
            "is_safe": False,
            "sanitization_status": "ERROR",
            "filter_match_state": "ERROR",
            "violations": [str(e)],
            "filter_results": {},
            "template_name": template_name,
            "response_text": response_text,
        }


# =====================================================================
# 2. SENSITIVE DATA PROTECTION (DLP & REGEX MASKING)
# =====================================================================

# Regex patterns for ADS domain specifics and general PII/SPII
REGEX_PATTERNS = {
    "DRIVER_ID": re.compile(
        r"\b(?:Driver\s*(?:ID|License|#)?[:\s-]*|DL[:\s-]*)?([A-Z]{1,2}[0-9]{6,8}|D[0-9]{6,8})\b",
        re.IGNORECASE,
    ),
    "PHONE_NUMBER": re.compile(
        r"(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b"
    ),
    "COORDINATES": re.compile(
        r"[-+]?(?:[1-8]?\d(?:\.\d+)?|90(?:\.0+)?)[°]?\s*[NS]?[,\s]+[-+]?(?:180(?:\.0+)?|(?:1[0-7]\d|[1-9]?\d)(?:\.\d+)?)[°]?\s*[EW]?"
    ),
    "EMAIL_ADDRESS": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    ),
    "US_SSN": re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b"
    ),
}

DLP_INFO_TYPES = [
    {"name": "PHONE_NUMBER"},
    {"name": "EMAIL_ADDRESS"},
    {"name": "PERSON_NAME"},
    {"name": "US_DRIVERS_LICENSE_NUMBER"},
    {"name": "US_SOCIAL_SECURITY_NUMBER"},
    {"name": "LOCATION"},
]


def get_dlp_client() -> dlp_v2.DlpServiceClient:
    """Initializes Google Cloud DLP client."""
    return dlp_v2.DlpServiceClient()


def scan_sensitive_data(
    text: str,
    project_id: Optional[str] = None,
    use_dlp: bool = True,
) -> Dict[str, Any]:
    """Scans text for PII/SPII (e.g. driver IDs, phone numbers, GPS coordinates) using Cloud DLP and regex.

    Args:
        text: Text to scan.
        project_id: GCP project ID.
        use_dlp: Whether to invoke Google Cloud DLP API in addition to regex scan.

    Returns:
        Dict with 'has_pii' (bool), 'findings' (list of findings), and 'count' (int).
    """
    findings: List[Dict[str, Any]] = []
    proj = project_id or DEFAULT_PROJECT_ID

    # 1. Regex Scan (always runs for domain-specific patterns like GPS coordinates and Driver IDs)
    for info_type, pattern in REGEX_PATTERNS.items():
        for match in pattern.finditer(text):
            val = match.group(0).strip()
            # Basic sanity check to avoid matching harmless single numbers
            if val and len(val) >= 4:
                findings.append({
                    "info_type": info_type,
                    "quote": val,
                    "start": match.start(),
                    "end": match.end(),
                    "source": "REGEX",
                })

    # 2. Cloud DLP API Scan (if enabled and project ID available)
    if use_dlp and proj and text.strip():
        try:
            dlp_client = get_dlp_client()
            parent = f"projects/{proj}/locations/global"
            inspect_config = {
                "info_types": DLP_INFO_TYPES,
                "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
                "include_quote": True,
            }
            item = {"value": text}
            response = dlp_client.inspect_content(
                request={"parent": parent, "inspect_config": inspect_config, "item": item}
            )
            for finding in response.result.findings:
                findings.append({
                    "info_type": finding.info_type.name,
                    "quote": finding.quote,
                    "likelihood": dlp_v2.Likelihood(finding.likelihood).name,
                    "source": "DLP",
                })
        except Exception as e:
            logger.warning(f"Cloud DLP inspection encountered error ({e}); using regex findings.")

    has_pii = len(findings) > 0
    logger.debug(
        f"Sensitive data scan completed: has_pii={has_pii}, count={len(findings)}",
        extra={"has_pii": has_pii, "findings_count": len(findings)},
    )

    return {
        "has_pii": has_pii,
        "count": len(findings),
        "findings": findings,
    }


def mask_sensitive_data(
    text: str,
    project_id: Optional[str] = None,
    use_dlp: bool = True,
) -> str:
    """Masks PII/SPII tokens (driver IDs, phone numbers, GPS coordinates, emails) from text.

    Args:
        text: String containing sensitive payload data.
        project_id: GCP project ID.
        use_dlp: Whether to use Cloud DLP de-identification before regex masking.

    Returns:
        Sanitized text with sensitive tokens replaced by redacted placeholders.
    """
    masked = text
    proj = project_id or DEFAULT_PROJECT_ID

    # 1. Cloud DLP de-identification for standard PII (phone, email, person name, SSN)
    if use_dlp and proj and masked.strip():
        try:
            dlp_client = get_dlp_client()
            parent = f"projects/{proj}/locations/global"
            deidentify_config = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {}
                            }
                        }
                    ]
                }
            }
            inspect_config = {
                "info_types": [
                    {"name": "PHONE_NUMBER"},
                    {"name": "EMAIL_ADDRESS"},
                    {"name": "US_SOCIAL_SECURITY_NUMBER"},
                    {"name": "US_DRIVERS_LICENSE_NUMBER"},
                ],
                "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
            }
            response = dlp_client.deidentify_content(
                request={
                    "parent": parent,
                    "deidentify_config": deidentify_config,
                    "inspect_config": inspect_config,
                    "item": {"value": masked},
                }
            )
            masked = response.item.value
        except Exception as e:
            logger.warning(f"Cloud DLP de-identify failed ({e}); falling back to regex masking.")

    # 2. Regex Masking for Driver IDs, coordinates, phone numbers, emails, and SSNs
    masked = REGEX_PATTERNS["DRIVER_ID"].sub("[REDACTED_DRIVER_ID]", masked)
    masked = REGEX_PATTERNS["COORDINATES"].sub("[REDACTED_COORDINATES]", masked)
    masked = REGEX_PATTERNS["PHONE_NUMBER"].sub("[REDACTED_PHONE]", masked)
    masked = REGEX_PATTERNS["EMAIL_ADDRESS"].sub("[REDACTED_EMAIL]", masked)
    masked = REGEX_PATTERNS["US_SSN"].sub("[REDACTED_SSN]", masked)

    return masked


def sanitize_payload_for_storage(
    payload: Union[str, Dict[str, Any], List[Any]],
    project_id: Optional[str] = None,
) -> Union[str, Dict[str, Any], List[Any]]:
    """Recursively scans and masks sensitive data in structured or text payloads.

    Args:
        payload: String, list, or dict containing operational payload.
        project_id: GCP project ID.

    Returns:
        Sanitized payload with all PII/SPII masked.
    """
    if isinstance(payload, str):
        return mask_sensitive_data(payload, project_id=project_id)
    elif isinstance(payload, dict):
        return {k: sanitize_payload_for_storage(v, project_id=project_id) for k, v in payload.items()}
    elif isinstance(payload, list):
        return [sanitize_payload_for_storage(item, project_id=project_id) for item in payload]
    return payload


# =====================================================================
# 3. GEMINI SAFETY SETTINGS & CONFIGURATION
# =====================================================================

def get_safety_settings(
    threshold: types.HarmBlockThreshold = types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
) -> List[types.SafetySetting]:
    """Defines explicit safety settings for Gemini content generation.

    Configures thresholds for:
    - Hate Speech (HARM_CATEGORY_HATE_SPEECH)
    - Dangerous Content (HARM_CATEGORY_DANGEROUS_CONTENT)
    - Harassment (HARM_CATEGORY_HARASSMENT)
    - Sexually Explicit (HARM_CATEGORY_SEXUALLY_EXPLICIT)

    Args:
        threshold: Desired HarmBlockThreshold (default BLOCK_LOW_AND_ABOVE).

    Returns:
        List of configured types.SafetySetting objects.
    """
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=threshold,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=threshold,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=threshold,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=threshold,
        ),
    ]


def get_generate_content_config(
    temperature: float = 0.2,
    top_p: float = 0.95,
    max_output_tokens: Optional[int] = 2048,
    safety_threshold: types.HarmBlockThreshold = types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    system_instruction: Optional[str] = None,
    model_armor_template: Optional[str] = None,
    **kwargs,
) -> types.GenerateContentConfig:
    """Builds a reusable GenerateContentConfig with explicit safety thresholds.

    Args:
        temperature: Model sampling temperature.
        top_p: Nucleus sampling parameter.
        max_output_tokens: Maximum tokens to generate.
        safety_threshold: HarmBlockThreshold to apply across all categories.
        system_instruction: Optional system instruction for Gemini.
        model_armor_template: Optional Model Armor template resource name.
        **kwargs: Additional GenerateContentConfig parameters.

    Returns:
        google.genai.types.GenerateContentConfig instance.
    """
    safety_settings = get_safety_settings(threshold=safety_threshold)

    armor_config = None
    if model_armor_template:
        armor_config = types.ModelArmorConfig(
            prompt_template_name=model_armor_template,
            response_template_name=model_armor_template,
        )

    config = types.GenerateContentConfig(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
        safety_settings=safety_settings,
        system_instruction=system_instruction,
        model_armor_config=armor_config,
        **kwargs,
    )

    logger.debug(
        "Created GenerateContentConfig with explicit enterprise safety settings",
        extra={"temperature": temperature, "safety_threshold": str(safety_threshold)},
    )
    return config


# Reusable default configuration for ADS Unstructured Document Synthesis
DEFAULT_GENERATE_CONTENT_CONFIG = get_generate_content_config(
    temperature=0.2,
    top_p=0.95,
    max_output_tokens=2048,
    safety_threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    system_instruction=(
        "You are an enterprise AI document synthesis assistant for the Alaska Department of Snow (ADS). "
        "Strictly adhere to operational safety, data governance, and public safety dispatch protocols."
    ),
)
