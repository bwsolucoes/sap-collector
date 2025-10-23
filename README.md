# Coletor Simplificado de Logs SAP ALM para Datadog

## Visão Geral

Este serviço coleta logs JSON brutos de endpoints configuráveis da API SAP Cloud ALM e os envia diretamente para o endpoint de ingestão de logs do Datadog. O parsing e a extração de dados dos logs devem ser configurados na plataforma Datadog.

Projetado para rodar como um contêiner em Kubernetes (AKS no Azure, EKS na AWS, etc.).

## Pré-requisitos

* Cluster Kubernetes (ex: Azure Kubernetes Service - AKS).
* `kubectl` configurado para o cluster.
* Acesso a um registro de contêiner (ex: Azure Container Registry - ACR).
* Credenciais válidas para API SAP Cloud ALM (OAuth2 Client ID/Secret, URL do Token).
* Credenciais válidas para API Datadog (API Key, URL de Ingestão).
* Docker para construir a imagem.

## Arquivos do Projeto

* `main_simplified.py`: Script Python principal.
* `requirements.txt`: Dependências (`requests`).
* `Dockerfile`: Para construir a imagem do contêiner.
* `config.ini.example`: Template do arquivo de configuração.

## Configuração

A configuração é gerenciada através de um arquivo `config.ini`, que **deve ser injetado no contêiner como um Secret do Kubernetes**.

1.  **Crie `config.ini`:** Copie `config.ini.example` e renomeie para `config.ini`. **Não adicione este arquivo ao Git.**
2.  **Preencha as Seções:**
    * `[general]`: Defina o `collection_interval_seconds`.
    * `[logging]` (Opcional): Configure o path para log em arquivo dentro do contêiner.
    * `[sap_auth]`: Insira as credenciais OAuth2 (`client_id`, `client_secret`, `token_url`).
    * `[sap_endpoints]`: Defina as URLs completas da API SAP ALM a serem coletadas.
        * A chave (ex: `idoc_logs`) será usada como parte do `ddsource` e `ddtags` no Datadog.
        * **Importante:** Use `%%` para escapar qualquer caractere `%` nas URLs (ex: `ABAP%%20Web...`).
    * `[datadog]`: Insira `api_key`, `log_url` e opcionalmente `env_tag`.

## Passos de Implementação (Adaptar à Pipeline de CI/CD)

1.  **Construir e Publicar Imagem Docker:**
    * Construa a imagem usando o `Dockerfile`.
    * Envie a imagem para seu registro (ex: ACR).
    ```bash
    # Exemplo (substitua pelos seus valores):
    REGISTRY_URL="seuacr.azurecr.io"
    IMAGE_NAME="api-collector-sap"
    IMAGE_TAG="v1.0"

    docker build -t $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG .
    docker push $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG
    ```

2.  **Preparar Manifesto Kubernetes:**
    * Edite `k8s/deployment.yaml`.
    * Atualize `spec.template.spec.containers[0].image` com a URL completa da imagem publicada.

3.  **Implantar no Kubernetes (AKS):**
    * **Criar/Atualizar o Secret:** A pipeline (ou processo manual seguro) deve criar o Secret `api-config` no namespace de destino, usando o `config.ini` preenchido.
        ```bash
        # Exemplo Manual (PREFERIR VIA PIPELINE SEGURA):
        kubectl delete secret api-config --ignore-not-found
        kubectl create secret generic api-config --from-file=config.ini=/caminho/para/seu/config.ini_real
        ```
    * **Aplicar o Deployment:**
        ```bash
        kubectl apply -f k8s/deployment.yaml
        ```

## Validação

1.  **Status do Pod:** `kubectl get pods -l app=api-collector` (aguarde `Running`).
2.  **Logs do Pod:** `kubectl logs -f <nome-do-pod-api-collector>`. Verifique se há logs de coleta e envio bem-sucedidos ou mensagens de erro.
3.  **Datadog:** Verifique a chegada dos logs com `source` `sap_cloud_alm_<chave_do_endpoint>` (ex: `sap_cloud_alm_idoc_logs`). Configure Pipelines de Log no Datadog para parsear o JSON no campo `message`.

## Compatibilidade Azure (AKS)

* Este código e os manifestos Kubernetes são **compatíveis** com Azure Kubernetes Service (AKS). Os componentes são padrão Docker/Kubernetes.
* A pipeline de CI/CD do cliente precisará ser configurada para interagir com os recursos do Azure (ACR para imagens, `az aks get-credentials` para acesso ao cluster, gerenciamento seguro de segredos para criar o `api-config`).
