# Customer Service Transcript Parser - Enterprise Cloud Architecture

## 1. Executive Summary

This document specifies the enterprise-grade Google Cloud Platform (GCP) target architecture for the **Customer Service Transcript Parser** application. The solution ingests unstructured audio and text call transcripts, extracts structured business entities (Call ID, Customer, Agent, Issue, Resolution, Escalation status) using Gemini on Vertex AI, maintains an immutable processing audit trail, and persists analytical records directly into Google BigQuery.

---

## 2. End-to-End Architectural Diagram (Mermaid)

```mermaid
flowchart TD
    %% Styling Definitions
    classDef client fill:#E8F0FE,stroke:#4285F4,stroke-width:2px,color:#174EA6;
    classDef security fill:#FCE8E6,stroke:#EA4335,stroke-width:2px,color:#B31412;
    classDef compute fill:#E6F4EA,stroke:#34A853,stroke-width:2px,color:#137333;
    classDef data fill:#FEF7E0,stroke:#FBBC04,stroke-width:2px,color:#B06000;
    classDef cicd fill:#F3E8FD,stroke:#9334E6,stroke-width:2px,color:#6200EA;
    classDef network fill:#E0F2F1,stroke:#009688,stroke-width:2px,color:#004D40;

    %% Client & Ingress Layer
    subgraph Client_Layer ["Client & Ingress Layer"]
        User["Support Staff / Ops User\n(Browser Web UI)"]:::client
        APIClient["External CRM / Ticketing System\n(REST API Client)"]:::client
        LB["Cloud Load Balancing / HTTPS Ingress\nTLS 1.3 Termination & DDoS Protection"]:::security
    end

    %% CI/CD Automation Pipeline
    subgraph CICD_Pipeline ["CI/CD Deployment Pipeline"]
        GitRepo["Git Repository\n(Cloud Source / GitHub)"]:::cicd
        CB_Trigger["Cloud Build Trigger\n(Automated on Commit)"]:::cicd
        CB_Runner["Cloud Build Worker Pool\n(Build, Tag, Test, Apply)"]:::cicd
        AR["Artifact Registry\n(Docker Image Repository)"]:::cicd
        GCS_State[("Cloud Storage Bucket\n(Terraform State + Object Versioning)")]:::cicd
    end

    %% Isolated VPC Network & Compute
    subgraph VPC_Boundary ["Dedicated VPC Network (vpc-customer-service)"]
        subgraph Subnet_VPC ["Private Subnetwork (10.0.1.0/24)"]
            VPC_Connector["Serverless VPC Access Connector\n(vpc-conn-transcript : 10.8.0.0/28)"]:::network
            Cloud_NAT["Cloud NAT Gateway & Router\n(Egress to Private Services)"]:::network
            PGA["Private Google Access\n(googleapis.com internal VIP)"]:::network
        end

        subgraph Cloud_Run_Layer ["Serverless Execution Layer"]
            CR_Service["Cloud Run v2 Service\n(customer-service-parser)\nRuntime: Python 3.12 / Gunicorn / Flask"]:::compute
            CR_SA["Service Account: sa-customer-service-app\n(Least-Privilege RBAC)"]:::security
        end
    end

    %% Security & Managed Services Layer
    subgraph Managed_Services ["Google Cloud Managed Services"]
        SecretMgr["Secret Manager\n(customer-service-flask-secret-key)"]:::security
        VertexAI["Vertex AI / Gemini API\n(gemini-2.5-flash Semantic Parser)"]:::data
        
        subgraph BigQuery_Warehouse ["BigQuery Analytics Warehouse"]
            BQ_Dataset["Dataset: customer_service"]:::data
            BQ_Table[("Table: transcripts\n- call_id, customer, agent, product\n- parsed_transcript (RECORD)\n- log_history (REPEATED RECORD)")]:::data
            BQ_Mirror[("Table: customer_service_transcripts\n(Compatibility Mirror)")]:::data
        end
    end

    %% Connections - Client to Compute
    User -->|HTTPS :443| LB
    APIClient -->|HTTPS :443 /api/upload| LB
    LB -->|Secure Ingress| CR_Service

    %% Connections - CI/CD Pipeline
    GitRepo -->|Push / PR Event| CB_Trigger
    CB_Trigger -->|Execute Pipeline| CB_Runner
    CB_Runner -->|1. Build & Push Image| AR
    CB_Runner -->|2. Pull & Lock State| GCS_State
    CB_Runner -->|3. Terraform Apply| VPC_Boundary
    CB_Runner -->|4. Deploy Container Revision| CR_Service

    %% Connections - Compute Network & Security
    CR_Service -.->|Assumes Identity| CR_SA
    CR_Service -->|All Egress Traffic| VPC_Connector
    VPC_Connector --> Subnet_VPC
    Subnet_VPC --> PGA
    VPC_Connector -.-> Cloud_NAT

    %% Connections - Private Service Consumption
    CR_Service -->|Mount Secret| SecretMgr
    PGA -->|Semantic Extraction (Private IP)| VertexAI
    PGA -->|Stream Records & Query History (Private IP)| BQ_Dataset
    BQ_Dataset --> BQ_Table
    BQ_Dataset --> BQ_Mirror
```

---

## 3. Detailed ASCII Visual Architecture Map

```text
========================================================================================================================
                                      CUSTOMER SERVICE TRANSCRIPT PARSER ARCHITECTURE
========================================================================================================================

    [ Support Operator / Browser ]           [ Enterprise CRM / API Client ]
                 │                                          │
                 └────────────────────┬─────────────────────┘
                                      │ HTTPS (TLS 1.3 / Port 443)
                                      ▼
             ┌─────────────────────────────────────────────────────────┐
             │       Google Cloud Ingress & Load Balancing             │
             │       - Global Anycast IP & SSL Termination             │
             │       - Cloud Armor WAF & Anti-DDoS Protection           │
             └────────────────────────┬────────────────────────────────┘
                                      │
                                      ▼
 ╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
 ║  GCP PROJECT: qwiklabs-gcp-04-c9826cb9816d (Region: us-central1)                                                   ║
 ║                                                                                                                     ║
 ║   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
 ║   │ CI/CD PIPELINE (Cloud Build & Artifact Registry)                                                            │   ║
 ║   │                                                                                                             │   ║
 ║   │  [ Git Repo ] ──► [ Cloud Build Trigger ]                                                                   │   ║
 ║   │                              │                                                                              │   ║
 ║   │                              ▼                                                                              │   ║
 ║   │   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐   │   ║
 ║   │   │ Cloud Build Execution Steps (Service Account: sa-cloudbuild-deployer)                               │   │   ║
 ║   │   │  1. Build Docker Container (Python 3.12, Gunicorn, Flask)                                           │   │   ║
 ║   │   │  2. Push image ──► [ Artifact Registry: us-central1-docker.pkg.dev/.../transcript-parser:tag ]       │   │   ║
 ║   │   │  3. Terraform Init (State Bucket ──► gs://<PROJECT_ID>-tfstate [Object Versioning: Enabled])       │   │   ║
 ║   │   │  4. Terraform Plan (Diff validation & dry-run execution)                                            │   │   ║
 ║   │   │  5. Terraform Apply (VPC, Subnets, IAM, BigQuery, Secret Manager, Cloud Run)                        │   │   ║
 ║   │   │  6. Deployment verification & health check probe                                                    │   │   ║
 ║   │   └─────────────────────────────────────────────────────────────────────────────────────────────────────┘   │   ║
 ║   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
 ║                                                                                                                     ║
 ║   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
 ║   │ DEDICATED VPC NETWORK: vpc-customer-service (Data Isolation)                                                │   ║
 ║   │                                                                                                             │   ║
 ║   │  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐  │   ║
 ║   │  │ Private Subnetwork: sb-customer-service-private (10.0.1.0/24)                                         │  │   ║
 ║   │  │ [ Private Google Access (PGA): ENABLED ]                                                              │  │   ║
 ║   │  │                                                                                                       │  │   ║
 ║   │  │   ┌───────────────────────────────────┐               ┌───────────────────────────────────────────┐   │  │   ║
 ║   │  │   │ Serverless VPC Access Connector   │               │ Cloud Router & NAT Gateway                │   │  │   ║
 ║   │  │   │ Name: vpc-conn-transcript         │               │ - Name: vpc-customer-service-nat          │   │  │   ║
 ║   │  │   │ CIDR: 10.8.0.0/28 (e2-micro)      │               │ - Auto IP allocation for outbound egress  │   │  │   ║
 ║   │  │   └─────────────────▲─────────────────┘               └───────────────────────────────────────────┘   │  │   ║
 ║   │  └─────────────────────┼─────────────────────────────────────────────────────────────────────────────────┘  │   ║
 ║   └────────────────────────┼────────────────────────────────────────────────────────────────────────────────────┘   ║
 ║                            │                                                                                        ║
 ║                            │ VPC Egress (ALL_TRAFFIC)                                                               ║
 ║   ┌────────────────────────┴────────────────────────────────────────────────────────────────────────────────────┐   ║
 ║   │ COMPUTE LAYER: Cloud Run v2 Service (customer-service-parser)                                               │   ║
 ║   │                                                                                                             │   ║
 ║   │  - Container: ${REGION}-docker.pkg.dev/${PROJECT_ID}/customer-service-parser-repo/transcript-parser:latest   │   ║
 ║   │  - Runtime: Python 3.12 Slim, Gunicorn (2 Workers, 4 Threads), Flask REST API + Responsive Web UI            │   ║
 ║   │  - Service Account: sa-customer-service-app@<PROJECT_ID>.iam.gserviceaccount.com                            │   ║
 ║   │  - Environment: PORT=8080, BIGQUERY_DATASET=customer_service, BIGQUERY_TABLE=transcripts                    │   ║
 ║   │  - Secret Ref: FLASK_SECRET_KEY ◄── [ Secret Manager: customer-service-flask-secret-key ]                   │   ║
 ║   └────────────────────────┬────────────────────────────────────────────────────────────────────────────────────┘   ║
 ║                            │                                                                                        ║
 ║                            │ Internal Network Route via Private Google Access (No Public Internet Transit)         ║
 ║                            ▼                                                                                        ║
 ║   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
 ║   │ MANAGED GOOGLE CLOUD SERVICES (Internal Private Endpoints)                                                  │   ║
 ║   │                                                                                                             │   ║
 ║   │  1. Vertex AI / Gemini API (us-central1-aiplatform.googleapis.com)                                          │   ║
 ║   │     - Model: gemini-2.5-flash                                                                               │   ║
 ║   │     - Action: Semantic parsing, metadata extraction, validation, error retry backoff                        │   ║
 ║   │                                                                                                             │   ║
 ║   │  2. Google BigQuery Analytics Warehouse (bigquery.googleapis.com)                                           │   ║
 ║   │     - Dataset: customer_service (Location: US)                                                              │   ║
 ║   │     - Primary Table: transcripts                                                                            │   ║
 ║   │         * call_id, date, customer, agent, product, issue, resolution, escalate (STRING)                     │   ║
 ║   │         * parsed_transcript (RECORD)                                                                        │   ║
 ║   │         * original_transcript (STRING)                                                                      │   ║
 ║   │         * log_history (RECORD, REPEATED) -> timestamp, level, stage, message, details                       │   ║
 ║   │         * status (STRING), processed_at (TIMESTAMP), model_name (STRING), execution_time_seconds (FLOAT)    │   ║
 ║   │     - Compatibility Table: customer_service_transcripts                                                     │   ║
 ║   │                                                                                                             │   ║
 ║   │  3. Google Secret Manager (secretmanager.googleapis.com)                                                    │   ║
 ║   │     - Secret: customer-service-flask-secret-key (Automatic replication, rotation-ready)                     │   ║
 ║   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
 ╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## 4. Key Security & Architectural Principles

### 4.1 Data Isolation & Network Security
- **Dedicated VPC Network (`vpc-customer-service`)**: Custom VPC with no default subnet auto-creation.
- **Private Subnet (`sb-customer-service-private`, `10.0.1.0/24`)**: Workloads are isolated from public subnets.
- **Private Google Access (`private_ip_google_access = true`)**:
  - Outbound traffic from Cloud Run directed to BigQuery (`bigquery.googleapis.com`) and Vertex AI (`aiplatform.googleapis.com`) traverses Google's private software-defined network.
  - Zero compute packets travel across the public Internet.
- **Serverless VPC Access Connector (`vpc-conn-transcript`, `10.8.0.0/28`)**:
  - Connects Cloud Run instances directly to the private VPC subnet.
  - Egress configuration set to `ALL_TRAFFIC`, guaranteeing that all outbound traffic routes strictly through the VPC and Cloud NAT/Private Google Access.

### 4.2 Strict Least-Privilege IAM Roles
Two distinct Service Accounts are provisioned with separation of duties:

| Service Account | Role | Purpose / Scope |
| :--- | :--- | :--- |
| `sa-customer-service-app` | `roles/bigquery.dataEditor` | Append and stream parsed transcript records into BigQuery |
| `sa-customer-service-app` | `roles/bigquery.jobUser` | Execute queries to populate the UI history view |
| `sa-customer-service-app` | `roles/aiplatform.user` | Send prompt inference requests to Gemini 2.5 Flash |
| `sa-customer-service-app` | `roles/secretmanager.secretAccessor` | Access `FLASK_SECRET_KEY` secret payload at container startup |
| `sa-customer-service-app` | `roles/vpcaccess.user` | Connect through the Serverless VPC Access connector |
| `sa-cloudbuild-deployer` | `roles/run.admin` | Deploy revisions to Cloud Run |
| `sa-cloudbuild-deployer` | `roles/iam.serviceAccountUser` | Act as `sa-customer-service-app` during Cloud Run provisioning |
| `sa-cloudbuild-deployer` | `roles/artifactregistry.writer` | Push compiled container images to Artifact Registry |
| `sa-cloudbuild-deployer` | `roles/storage.admin` | Read and write state in the GCS Terraform remote state bucket |
| `sa-cloudbuild-deployer` | `roles/secretmanager.secretAccessor` | Read build-time secrets if needed |

### 4.3 Secure Remote State & Secret Management
- **GCS Remote State**:
  - State stored at `gs://<PROJECT_ID>-tfstate/transcript-parser/state/default.tfstate`.
  - Object versioning enabled to prevent state corruption or inadvertent deletions.
  - Uniform Bucket-Level Access (UBLA) enforced.
- **Secret Manager**:
  - Sensitive cryptographic seeds (`FLASK_SECRET_KEY`) stored encrypted at rest with Google-managed or customer-managed keys.
  - Mounted dynamically into Cloud Run container memory at runtime without committing secrets into source control or Docker images.

---

## 5. BigQuery Data Schema & Entity Mapping

The BigQuery table schema matches the Pydantic data model (`models.py`) and semantic parser payload (`parser.py`):

```json
[
  {"name": "call_id", "type": "STRING", "mode": "NULLABLE", "description": "Call reference ID"},
  {"name": "date", "type": "STRING", "mode": "NULLABLE", "description": "Interaction timestamp"},
  {"name": "customer", "type": "STRING", "mode": "NULLABLE", "description": "Customer name"},
  {"name": "agent", "type": "STRING", "mode": "NULLABLE", "description": "Agent name"},
  {"name": "product", "type": "STRING", "mode": "NULLABLE", "description": "Product discussed"},
  {"name": "issue", "type": "STRING", "mode": "NULLABLE", "description": "Issue summary"},
  {"name": "resolution", "type": "STRING", "mode": "NULLABLE", "description": "Resolution notes"},
  {"name": "escalate", "type": "STRING", "mode": "NULLABLE", "description": "Escalation indicator"},
  {
    "name": "parsed_transcript",
    "type": "RECORD",
    "mode": "NULLABLE",
    "fields": [
      {"name": "call_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "date", "type": "STRING", "mode": "NULLABLE"},
      {"name": "customer", "type": "STRING", "mode": "NULLABLE"},
      {"name": "agent", "type": "STRING", "mode": "NULLABLE"},
      {"name": "product", "type": "STRING", "mode": "NULLABLE"},
      {"name": "issue", "type": "STRING", "mode": "NULLABLE"},
      {"name": "resolution", "type": "STRING", "mode": "NULLABLE"},
      {"name": "escalate", "type": "STRING", "mode": "NULLABLE"}
    ]
  },
  {"name": "original_transcript", "type": "STRING", "mode": "NULLABLE"},
  {
    "name": "log_history",
    "type": "RECORD",
    "mode": "REPEATED",
    "fields": [
      {"name": "timestamp", "type": "STRING", "mode": "NULLABLE"},
      {"name": "level", "type": "STRING", "mode": "NULLABLE"},
      {"name": "stage", "type": "STRING", "mode": "NULLABLE"},
      {"name": "message", "type": "STRING", "mode": "NULLABLE"},
      {"name": "details", "type": "STRING", "mode": "NULLABLE"}
    ]
  },
  {"name": "status", "type": "STRING", "mode": "NULLABLE"},
  {"name": "source_file", "type": "STRING", "mode": "NULLABLE"},
  {"name": "processed_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
  {"name": "model_name", "type": "STRING", "mode": "NULLABLE"},
  {"name": "execution_time_seconds", "type": "FLOAT", "mode": "NULLABLE"}
]
```

---

## 6. Operational Execution & Runbook

### Deploying via Automated Script
```bash
chmod +x deploy.sh
./deploy.sh -y
```

### Deploying via CI/CD Pipeline
Commit changes to the main branch or invoke Cloud Build manually:
```bash
gcloud builds submit --config=cloudbuild.yaml .
```

### Verifying Service Health
```bash
curl -s "$(gcloud run services describe customer-service-parser --region=us-central1 --format='value(status.url)')/health"
```
