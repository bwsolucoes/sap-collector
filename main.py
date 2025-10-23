import configparser
import json
import logging
import logging.handlers
import os
import sys
import time
from typing import Optional, Dict, List, Any

import requests

# --- Configuração do Logging ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)

# --- Constantes ---
# Caminho padrão para o arquivo de configuração dentro do container
CONFIG_PATH = '/app/config.ini'

# --- Funções Auxiliares ---

def load_config(config_path: str = CONFIG_PATH) -> configparser.ConfigParser:
    """Carrega as configurações do arquivo INI, desabilitando interpolação."""
    config = configparser.ConfigParser(interpolation=None) # Crucial para URLs com %
    if not os.path.exists(config_path):
        logger.error(f"Arquivo de configuração '{config_path}' não encontrado.")
        sys.exit(1)
    try:
        config.read(config_path)
        logger.info(f"Arquivo de configuração '{config_path}' carregado com sucesso.")
        return config
    except configparser.Error as e:
        logger.error(f"Erro ao ler o arquivo de configuração '{config_path}': {e}")
        sys.exit(1)

def setup_file_logging(config: configparser.ConfigParser):
    """Configura o logging para arquivo com rotação, se definido no config."""
    try:
        log_file_path = config.get('logging', 'log_file_path', fallback=None)
        if log_file_path:
            log_dir = os.path.dirname(log_file_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            rotation = config.get('logging', 'log_rotation_interval', fallback='D')
            backup_count = config.getint('logging', 'log_backup_count', fallback=7)
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_file_path, when=rotation, interval=1, backupCount=backup_count
            )
            file_handler.setFormatter(log_formatter)
            logger.addHandler(file_handler)
            logger.info(f"Logging configurado para o arquivo: {log_file_path}")
        else:
            logger.info("Logging para arquivo não configurado. Usando apenas console.")
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        logger.warning(f"Seção ou opção de logging ausente no config.ini: {e}. Usando apenas console.")
    except Exception as e:
        logger.error(f"Erro ao configurar logging para arquivo: {e}. Usando apenas console.")

def send_payload_to_datadog(payload: Any, config: configparser.ConfigParser, source_identifier: str):
    """Envia o payload JSON completo para o endpoint de logs do Datadog."""
    try:
        api_key = config.get('datadog', 'api_key')
        dd_url = config.get('datadog', 'log_url')
        env_tag = config.get('datadog', 'env_tag', fallback="env:not_set")

        headers = {'Content-Type': 'application/json', 'DD-API-KEY': api_key}
        hostname = os.getenv("HOSTNAME", "k8s-pod-unknown") # Prioriza hostname do K8s

        dd_payload = {
            "ddsource": f"sap_cloud_alm_{source_identifier}",
            "ddtags": f"{env_tag},sap_source:{source_identifier}",
            "hostname": hostname,
            "service": "sap-alm-log-collector",
            "message": payload
        }

        logger.info(f"Enviando payload da fonte '{source_identifier}' para Datadog...")
        response = requests.post(dd_url, headers=headers, json=dd_payload, timeout=15)
        response.raise_for_status()
        logger.info(f"Payload da fonte '{source_identifier}' enviado com sucesso para Datadog (Status: {response.status_code}).")

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao enviar payload '{source_identifier}' para Datadog: {e}")
    except configparser.NoOptionError as e:
        logger.error(f"Erro de configuração Datadog: Chave '{e.option}' não encontrada na seção '{e.section}'.")
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar payload '{source_identifier}' para Datadog: {e}")

# --- Funções SAP ---

def get_sap_token(config: configparser.ConfigParser) -> Optional[str]:
    """Obtém o token de acesso OAuth2 da SAP ALM."""
    logger.info("Tentando obter token de acesso SAP...")
    try:
        token_url = config.get('sap_auth', 'token_url')
        client_id = config.get('sap_auth', 'client_id')
        client_secret = config.get('sap_auth', 'client_secret')
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"grant_type": "client_credentials"}

        response = requests.post(
            token_url,
            data=payload,
            headers=headers,
            auth=(client_id, client_secret),
            timeout=20
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if token:
            logger.info("Token de acesso SAP obtido com sucesso.")
            return token
        else:
            logger.error("Token não encontrado na resposta da API SAP (access_token ausente).")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao obter token SAP: {e}")
        return None
    except configparser.NoOptionError as e:
        logger.error(f"Erro de configuração SAP Auth: Chave '{e.option}' não encontrada na seção '{e.section}'.")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar resposta JSON do token SAP: {e}. Resposta: {response.text[:200]}...")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao obter token SAP: {e}")
        return None

def fetch_sap_data(api_url: str, config: configparser.ConfigParser) -> Optional[Any]:
    """Busca dados de uma URL específica da API SAP ALM."""
    url_display = api_url.split('?')[0] # Para logs mais limpos
    logger.info(f"Buscando dados da API SAP: {url_display}...")
    access_token = get_sap_token(config)
    if not access_token:
        logger.error(f"Não foi possível obter token SAP. Abortando busca para {url_display}.")
        return None

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    try:
        response = requests.get(api_url, headers=headers, timeout=45)
        response.raise_for_status()

        try:
            payload = response.json()
            logger.info(f"Dados recebidos com sucesso da API SAP (Status: {response.status_code}). URL: {url_display}")
            if isinstance(payload, dict) and "resourceLogs" in payload:
                 logger.info(f"Payload contém {len(payload.get('resourceLogs', []))} resourceLog(s).")
            elif isinstance(payload, list):
                 logger.info(f"Payload é uma lista com {len(payload)} item(ns).")
            else:
                 logger.info("Payload recebido não é um dict com 'resourceLogs' nem uma lista.")
            return payload
        except json.JSONDecodeError as json_err:
            logger.error(f"Falha ao decodificar JSON da API SAP (Status: {response.status_code}). Erro: {json_err}. Resposta: {response.text[:500]}...")
            return None

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"Erro HTTP {http_err.response.status_code} ao buscar dados da API SAP ({url_display}): {http_err.response.reason}. Resposta: {http_err.response.text[:500]}...")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Erro de rede ao buscar dados da API SAP ({url_display}): {req_err}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar dados da API SAP ({url_display}): {e}")
        return None

# --- Bloco Principal ---

if __name__ == "__main__":
    config = load_config()
    setup_file_logging(config)

    # Carrega as URLs da seção [sap_endpoints] do config.ini
    sap_endpoints = {}
    try:
        if config.has_section('sap_endpoints'):
            sap_endpoints = dict(config.items('sap_endpoints'))
            if not sap_endpoints:
                 logger.critical("Seção [sap_endpoints] está vazia no config.ini. Nenhuma URL para buscar. Encerrando.")
                 sys.exit(1)
            logger.info(f"Carregadas {len(sap_endpoints)} URLs da seção [sap_endpoints].")
        else:
            logger.critical("Seção [sap_endpoints] não encontrada no config.ini. Nenhuma URL para buscar. Encerrando.")
            sys.exit(1)
    except Exception as e:
        logger.critical(f"Erro ao ler a seção [sap_endpoints] do config.ini: {e}. Encerrando.")
        sys.exit(1)

    try:
        interval = config.getint('general', 'collection_interval_seconds', fallback=300)
        logger.info(f"Iniciando coletor com intervalo de {interval} segundos.")

        while True:
            logger.info("--- Iniciando ciclo de coleta SAP ---")
            start_time = time.time()

            # Itera sobre as URLs carregadas do config.ini
            for source_id, url in sap_endpoints.items():
                logger.info(f"Processando fonte: {source_id}")
                if not url: # Pula se a URL estiver vazia no config
                    logger.warning(f"URL para fonte '{source_id}' está vazia no config.ini. Pulando.")
                    continue

                sap_payload = fetch_sap_data(url, config)

                if sap_payload is not None:
                    if sap_payload: # Verifica se não é vazio ({}, [])
                        send_payload_to_datadog(sap_payload, config, source_id)
                    else:
                        logger.info(f"Payload recebido da fonte '{source_id}' está vazio. Nenhum dado para enviar.")
                else:
                    logger.warning(f"Falha ao buscar dados da fonte '{source_id}'. Verifique logs de erro anteriores.")
                time.sleep(2) # Pausa entre requisições SAP

            end_time = time.time()
            elapsed = end_time - start_time
            logger.info(f"--- Ciclo de coleta SAP finalizado em {elapsed:.2f} segundos ---")

            wait_time = max(0, interval - elapsed)
            logger.info(f"Aguardando {wait_time:.2f} segundos para o próximo ciclo...")
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("Coletor interrompido manualmente.")
        sys.exit(0)
    except configparser.NoOptionError as e:
        logger.critical(f"Erro CRÍTICO de configuração: Chave '{e.option}' não encontrada na seção '{e.section}'. Encerrando.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Erro CRÍTICO inesperado no loop principal: {e}", exc_info=True)
        logger.info("Aguardando 60 segundos antes de tentar novamente...")
        time.sleep(60)
