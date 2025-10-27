import configparser
import json
import logging
import logging.handlers
import os
import sys
import time
from typing import Optional, Dict, List, Any
import xml.etree.ElementTree as ET # Importar para validar XML (opcional, mas bom para debug)

import requests

# --- Configuração do Logging ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)

# --- Constantes ---
CONFIG_PATH = '/app/config.ini'
# Adicionado para identificar a fonte especial
ABAP_WS_SOURCE_KEY_IDENTIFIER = "abap_ws" # Se a chave no config.ini contiver isso, usa a lógica de ERROR_CONTEXT

# --- Funções Auxiliares ---

def load_config(config_path: str = CONFIG_PATH) -> configparser.ConfigParser:
    """Carrega as configurações do arquivo INI, desabilitando interpolação."""
    config = configparser.ConfigParser(interpolation=None)
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

def send_to_datadog(message_payload: Any, resource_attributes: Dict[str, str], config: configparser.ConfigParser, source_identifier: str, record_id_for_log: str = "N/A"):
    """Função genérica para enviar um payload (JSON object ou string) para Datadog."""
    try:
        api_key = config.get('datadog', 'api_key')
        dd_url = config.get('datadog', 'log_url')
        env_tag = config.get('datadog', 'env_tag', fallback="env:not_set")

        headers = {'Content-Type': 'application/json', 'DD-API-KEY': api_key}
        hostname = os.getenv("HOSTNAME", "k8s-pod-unknown")

        resource_tags_list = [f"sap_resource_{k.replace('.', '_')}:{v}" for k, v in resource_attributes.items()]
        resource_tags = ",".join(resource_tags_list)

        ddtags = f"{env_tag},sap_source:{source_identifier}"
        if resource_tags:
            ddtags += f",{resource_tags}"

        dd_payload = {
            "ddsource": f"sap_cloud_alm_{source_identifier}",
            "ddtags": ddtags,
            "hostname": hostname,
            "service": "sap-alm-log-collector",
            "message": message_payload # Pode ser o logRecord ou o XML string
        }

        logger.info(f"Enviando log (ID: {record_id_for_log}) da fonte '{source_identifier}' para Datadog...")
        response = requests.post(dd_url, headers=headers, json=dd_payload, timeout=15)
        response.raise_for_status()
        logger.debug(f"Log (ID: {record_id_for_log}) da fonte '{source_identifier}' enviado com sucesso (Status: {response.status_code}).")

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao enviar log '{source_identifier}' para Datadog: {e}")
    except configparser.NoOptionError as e:
        logger.error(f"Erro de configuração Datadog: Chave '{e.option}' não encontrada na seção '{e.section}'.")
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar log '{source_identifier}' para Datadog: {e}")

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
        response = requests.post(token_url, data=payload, headers=headers, auth=(client_id, client_secret), timeout=20)
        response.raise_for_status()
        token = response.json().get("access_token")
        if token:
            logger.info("Token de acesso SAP obtido com sucesso.")
            return token
        else:
            logger.error("Token não encontrado na resposta da API SAP.")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao obter token SAP: {e}")
        return None
    except configparser.NoOptionError as e:
        logger.error(f"Erro de configuração SAP Auth: Chave '{e.option}' ausente na seção '{e.section}'.")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar resposta JSON do token SAP: {e}. Resposta: {response.text[:200]}...")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao obter token SAP: {e}")
        return None

def fetch_sap_data(api_url: str, config: configparser.ConfigParser) -> Optional[Any]:
    """Busca dados de uma URL específica da API SAP ALM."""
    url_display = api_url.split('?')[0]
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

def extract_resource_attributes(resource_log: Dict) -> Dict[str, str]:
    """Extrai atributos chave-valor simples do objeto resource."""
    resource_attributes = {}
    resource = resource_log.get("resource", {})
    if isinstance(resource, dict):
        attributes = resource.get("attributes", [])
        if isinstance(attributes, list):
            for attr in attributes:
                 if isinstance(attr, dict) and "key" in attr and isinstance(attr.get("value"), dict):
                     value_obj = attr.get("value")
                     simple_value = next((v for k, v in value_obj.items() if isinstance(v, (str, int, float, bool))), None)
                     if attr["key"] and simple_value is not None:
                         resource_attributes[attr["key"]] = str(simple_value)
    return resource_attributes

def find_error_context_xml(log_record: Dict) -> Optional[str]:
    """Procura pelo atributo 'ERROR_CONTEXT' e retorna seu stringValue (XML)."""
    attributes = log_record.get("attributes", [])
    if not isinstance(attributes, list):
        return None
    for attr in attributes:
        if isinstance(attr, dict) and attr.get("key") == "ERROR_CONTEXT":
            value_obj = attr.get("value")
            if isinstance(value_obj, dict):
                xml_string = value_obj.get("stringValue")
                if isinstance(xml_string, str):
                    # Validação opcional de XML (útil para debug)
                    # try:
                    #     ET.fromstring(xml_string)
                    #     logger.debug(f"XML encontrado em ERROR_CONTEXT é válido.")
                    # except ET.ParseError as xml_err:
                    #     logger.warning(f"Conteúdo de ERROR_CONTEXT não parece ser XML válido: {xml_err}. String: {xml_string[:100]}...")
                    return xml_string
    return None

# --- Bloco Principal ---

if __name__ == "__main__":
    # Define o path do config.ini (prioriza /app/config.ini se existir)
    effective_config_path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else 'config.ini'

    config = load_config(effective_config_path)
    setup_file_logging(config)

    sap_endpoints = {}
    try:
        if config.has_section('sap_endpoints'):
            sap_endpoints = dict(config.items('sap_endpoints'))
            if not sap_endpoints:
                 logger.critical("Seção [sap_endpoints] está vazia no config.ini. Encerrando.")
                 sys.exit(1)
            logger.info(f"Carregadas {len(sap_endpoints)} URLs da seção [sap_endpoints].")
        else:
            logger.critical("Seção [sap_endpoints] não encontrada no config.ini. Encerrando.")
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
            total_records_sent_cycle = 0

            for source_id, url in sap_endpoints.items():
                logger.info(f"Processando fonte: {source_id}")
                if not url:
                    logger.warning(f"URL para fonte '{source_id}' está vazia no config.ini. Pulando.")
                    continue

                # Determina se aplica a lógica especial para ABAP WS Provider
                is_abap_ws_source = ABAP_WS_SOURCE_KEY_IDENTIFIER in source_id.lower()
                if is_abap_ws_source:
                    logger.info(f"Fonte '{source_id}' identificada como ABAP WS Provider. Aplicando lógica de extração de ERROR_CONTEXT.")

                sap_payload = fetch_sap_data(url, config)
                records_sent_source = 0

                if sap_payload and isinstance(sap_payload, dict) and 'resourceLogs' in sap_payload:
                    resource_logs = sap_payload.get('resourceLogs', [])
                    if not isinstance(resource_logs, list):
                        logger.warning(f"Formato inesperado: 'resourceLogs' não é uma lista na resposta de '{source_id}'.")
                        continue

                    for resource_log in resource_logs:
                        if not isinstance(resource_log, dict): continue
                        res_attrs = extract_resource_attributes(resource_log)

                        scope_logs = resource_log.get('scopeLogs', [])
                        if not isinstance(scope_logs, list): continue

                        for scope_log in scope_logs:
                            if not isinstance(scope_log, dict): continue
                            log_records = scope_log.get('logRecords', [])
                            if not isinstance(log_records, list): continue

                            if not log_records:
                                logger.debug(f"Nenhum logRecord neste scopeLog para '{source_id}'.")
                                continue

                            logger.info(f"Encontrados {len(log_records)} logRecord(s) para processar da fonte '{source_id}'.")
                            for log_record in log_records:
                                if not isinstance(log_record, dict):
                                    logger.warning(f"Item inválido na lista logRecords: {type(log_record)}")
                                    continue

                                record_id = log_record.get("traceId", log_record.get("timeUnixNano", "N/A"))

                                # LÓGICA CONDICIONAL: ABAP WS vs Outros (IDoc)
                                if is_abap_ws_source:
                                    error_context_xml = find_error_context_xml(log_record)
                                    if error_context_xml:
                                        send_to_datadog(error_context_xml, res_attrs, config, source_id, record_id)
                                        records_sent_source += 1
                                    else:
                                        logger.debug(f"Registro {record_id} da fonte '{source_id}' não continha atributo 'ERROR_CONTEXT'. Pulando.")
                                else:
                                    # Lógica padrão (IDoc): envia o logRecord inteiro
                                    send_to_datadog(log_record, res_attrs, config, source_id, record_id)
                                    records_sent_source += 1

                            time.sleep(0.1) # Pausa curta após processar records de um scopeLog

                    logger.info(f"Total de {records_sent_source} logs enviados para a fonte '{source_id}'.")
                    total_records_sent_cycle += records_sent_source

                elif sap_payload is None:
                    logger.warning(f"Falha ao buscar dados da fonte '{source_id}'.")
                else:
                    logger.warning(f"Payload de '{source_id}' não contém 'resourceLogs' ou formato inesperado: {str(sap_payload)[:200]}...")

                time.sleep(2) # Pausa entre requisições SAP

            end_time = time.time()
            elapsed = end_time - start_time
            logger.info(f"--- Ciclo de coleta SAP finalizado em {elapsed:.2f} segundos. Total de {total_records_sent_cycle} logs enviados neste ciclo. ---")

            wait_time = max(0, interval - elapsed)
            logger.info(f"Aguardando {wait_time:.2f} segundos para o próximo ciclo...")
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("Coletor interrompido manualmente.")
        sys.exit(0)
    except configparser.NoOptionError as e:
        logger.critical(f"Erro CRÍTICO de config: Chave '{e.option}' ausente na seção '{e.section}'. Encerrando.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Erro CRÍTICO inesperado no loop principal: {e}", exc_info=True)
        logger.info("Aguardando 60 segundos antes de tentar novamente...")
        time.sleep(60)
