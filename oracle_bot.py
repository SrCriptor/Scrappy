import oci, threading, time, os, requests, urllib.request, json, ssl
from datetime import datetime

DISCORD_WEBHOOK = "https://discordapp.com/api/webhooks/1518755432368181398/qlhss1_k8E4U0dPdJ5fuy7ez-uCFGy7rB7Doib5n7c87j33zaF5FbQ5lEKgQ9A4w8Ey7"
STACKS = [
    "ocid1.ormstack.oc1.iad.amaaaaaafumdbciauyydukjvbfoqz6b3obmrbb6azrrttqnhu4hrsty4mnsq",
    "ocid1.ormstack.oc1.iad.amaaaaaafumdbcia4gij3gjkhbgnaez7yq2xioqb2iifj2zycczpbx5vlyxa",
    "ocid1.ormstack.oc1.iad.amaaaaaafumdbciahgvru56pjb45julp4l2gxbh5l3omgislo2elje4qncva"
]
historico_logs = []
lock_bot = threading.Lock()

def adicionar_log(texto):
    global historico_logs
    linha = f"[{datetime.now().strftime('%H:%M:%S')}] {texto}"
    # Limpa as tags HTML para o print real do terminal do servidor
    historico_logs.append(linha)
    if len(historico_logs) > 40: historico_logs.pop(0)

def atualizar_ultima_linha_log(texto):
    global historico_logs
    linha = f"[{datetime.now().strftime('%H:%M:%S')}] {texto}"
    if historico_logs:
        historico_logs[-1] = linha
    else:
        historico_logs.append(linha)

def atualizar_status_na_tentativa(ad_num, status):
    """Localiza a linha da tentativa e acopla o status nela com efeito Laranja Neon"""
    global historico_logs
    for i in range(len(historico_logs) - 1, -1, -1):
        if "Tentativa #" in historico_logs[i] and f"AD-{ad_num}" in historico_logs[i]:
            partes = historico_logs[i].split(" | ")
            prefixo_limpo = partes[0]
            
            # Efeito Laranja Neon com sombra projetada de alta intensidade
            estilo_laranja = "color: #FF8C00; font-weight: bold; text-shadow: 0 0 8px rgba(255, 140, 0, 0.6), 0 0 15px rgba(255, 140, 0, 0.4);"
            
            historico_logs[i] = f"{prefixo_limpo} | AD-{ad_num} ⚙️ Status: <span style='{estilo_laranja}'>{status}</span>"
            break


def contagem_regressiva_vermelha(segundos, mensagem_prefixo):
    global historico_logs
    
    # Efeito Vermelho Neon vibrante cobrindo toda a extensão da string
    estilo_vermelho = "color: #FF0000; font-weight: bold; text-shadow: 0 0 8px rgba(255, 0, 0, 0.6), 0 0 15px rgba(255, 0, 0, 0.4);"
    
    texto_base = f"<span style='{estilo_vermelho}'>⏱️ {mensagem_prefixo}: {segundos}s restantes...</span>"
    adicionar_log(texto_base)
    
    for i in range(segundos - 1, 0, -1):
        time.sleep(1)
        texto_dinamico = f"<span style='{estilo_vermelho}'>⏱️ {mensagem_prefixo}: {i}s restantes...</span>"
        atualizar_ultima_linha_log(texto_dinamico)
        
    time.sleep(1)
    if historico_logs:
        historico_logs.pop(-1) # Apaga o contador ao terminar

def pegar_logs():
    global historico_logs
    return "\n".join(historico_logs) if historico_logs else "[SISTEMA] Aguardando comando..."

# --- SISTEMA DE ENVIO DE EMBEDS COM REDUNDÂNCIA INFALÍVEL ---
def enviar_discord_embed(payload):
    def post_isolado():
        contexto = ssl._create_unverified_context()
        req_data = json.dumps(payload).encode('utf-8')
        
        try:
            req = urllib.request.Request(DISCORD_WEBHOOK, data=req_data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5, context=contexto): return
        except: pass
        
        try:
            url_alt = DISCORD_WEBHOOK.replace("discord.com", "discordapp.com")
            req = urllib.request.Request(url_alt, data=req_data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5, context=contexto): pass
        except Exception as e: print(f"[Discord Erro] {e}")

    threading.Thread(target=post_isolado, daemon=True).start()

def validar_e_montar_config():
    adicionar_log("[🔎 DIAGNÓSTICO] Verificando chaves de ambiente atualizadas...")
    fpt = os.environ.get("OCI_FINGERPRINT", "").strip()
    key = os.environ.get("OCI_PRIVATE_KEY", "").strip()
    if not fpt or not key:
        adicionar_log("❌ [CRÍTICO] Chave Privada ou Fingerprint ausentes!")
        return None
    key = key.replace("\\n", "\n").replace(" ", "\n")
    key = key.replace("BEGIN\nRSA\nPRIVATE\nKEY", "BEGIN RSA PRIVATE KEY").replace("END\nRSA\nPRIVATE\nKEY", "END RSA PRIVATE KEY")
    key = key.replace("BEGIN\nPRIVATE\nKEY", "BEGIN PRIVATE KEY").replace("END\nPRIVATE\nKEY", "END PRIVATE KEY")
    
    if not ("-----BEGIN RSA PRIVATE KEY-----" in key or "-----BEGIN PRIVATE KEY-----" in key):
        adicionar_log("❌ [CRÍTICO] Falta o cabeçalho '-----BEGIN PRIVATE KEY-----'!")
        return None

    config = {"user": os.environ.get("OCI_USER_OCID"), "fingerprint": fpt, "key_content": key, "tenancy": os.environ.get("OCI_TENANCY_OCID"), "region": os.environ.get("OCI_REGION", "us-ashburn-1")}
    try:
        oci.config.validate_config(config)
        adicionar_log("✅ [DIAGNÓSTICO] Estrutura aprovada!")
        return config
    except Exception as e:
        adicionar_log(f"❌ [CRÍTICO] Formato inválido: {e}")
        return None

def iniciar_auto_ping():
    time.sleep(30)
    url = os.environ.get("SPACE_URL")
    if not url: return
    while True:
        try: requests.get(url, timeout=10)
        except: pass
        time.sleep(600)

def loop_automacao_oracle():
    cfg = validar_e_montar_config()
    if not cfg:
        adicionar_log("🛑 [PARADO] Corrija os Secrets para liberar o loop.")
        return

    enviar_discord_embed({
        "embeds": [{
            "title": "🤖 Robô OCI - Inteligência Anti-Bloqueio Ativada",
            "description": "Script atualizado e rodando de forma oci-safe e assíncrona no Hugging Face.",
            "color": 3447003,
            "fields": [
                { "name": "⏱️ Tempo Base", "value": "60 segundos", "inline": True },
                { "name": "📍 Região", "value": cfg['region'], "inline": True }
            ],
            "footer": { "text": "Modo Resiliente Ativado" }
        }]
    })

    try:
        rm_client = oci.resource_manager.ResourceManagerClient(cfg)
        compute_client = oci.core.ComputeClient(cfg)
    except: return

    adicionar_log("[🛡️ TRAVA] Verificando instâncias criadas...")
    try:
        comp_id = os.environ.get("OCI_COMPARTMENT_ID") or cfg["tenancy"]
        vms = compute_client.list_instances(compartment_id=comp_id).data
        for vm in vms:
            if vm.shape == "VM.Standard.A1.Flex" and vm.lifecycle_state not in ["TERMINATED", "TERMINATING"]:
                adicionar_log(f"🛑 [BLOQUEADO] Uma VPS ARM já existe ('{vm.display_name}').")
                enviar_discord_embed({
                    "embeds": [{
                        "title": "🛡️ Trava de Segurança OCI",
                        "description": f"Loop suspenso. Uma VPS ARM já existente foi encontrada na sua conta: `{vm.display_name}`.",
                        "color": 15158332
                    }]
                })
                return
    except Exception as e: adicionar_log(f"[🛡️ TRAVA] Erro ao checar instâncias: {e}")

    adicionar_log("[SISTEMA 🟢] Nenhuma máquina ativa detectada. Conexão liberada.")
    tentativa, esp_padrao, esp_erro = 1, 60, 60

    while True:
        idx = (tentativa - 1) % 3
        ad_num = idx + 1
        adicionar_log(f"🔄 Tentativa #{tentativa} | AD-{ad_num}")
        
        op = oci.resource_manager.models.CreateApplyJobOperationDetails(operation="APPLY", execution_plan_strategy="AUTO_APPROVED")
        details = oci.resource_manager.models.CreateJobDetails(stack_id=STACKS[idx], job_operation_details=op, display_name=f"Oculto_HF_AD_{ad_num}")

        try:
            job_id = rm_client.create_job(details).data.id
            esp_erro = esp_padrao
            
            while True:
                status = rm_client.get_job(job_id).data.lifecycle_state
                atualizar_status_na_tentativa(ad_num, status)
                
                if status == "SUCCEEDED":
                    adicionar_log(f"🎉 SUCESSO! Criada no AD-{ad_num}!")
                    enviar_discord_embed({
                        "content": "@everyone 🎉 **A MÁQUINA FOI CRIADA!**",
                        "embeds": [{
                            "title": "✅ VPS Criada com Sucesso!",
                            "description": "A Stack do Resource Manager concluiu a execução com sucesso absoluto.",
                            "color": 3066993,
                            "fields": [
                                { "name": "📊 Total de Tentativas", "value": str(tentativa), "inline": True },
                                { "name": "📍 Local", "value": f"AD-{ad_num}", "inline": True }
                            ],
                            "footer": { "text": "Acesse o painel da OCI para pegar o IP" }
                        }]
                    })
                    return
                elif status in ["FAILED", "CANCELED"]:
                    adicionar_log(f"❌ Rejeitado: O Job terminou em {status} (Out of Capacity).")
                    enviar_discord_embed({
                        "embeds": [{
                            "title": f"❌ Falha na Tentativa #{tentativa} (AD-{ad_num})",
                            "description": "Recurso rejeitado pelo servidor da Oracle por falta de estoque (Out of Capacity).",
                            "color": 15158332,
                            "footer": { "text": "Rotacionando para o próximo AD..." }
                        }]
                    })
                    break
                
                contagem_regressiva_vermelha(30, f"Aguardando atualização do status AD-{ad_num}")
            
            contagem_regressiva_vermelha(120, "[PREVENÇÃO] Aguardando descanso pós-falha")
            tentativa += 1
            continue

        except oci.exceptions.ServiceError as e:
            adicionar_log(f"❌ API Recusou a Assinatura. Detalhe: {e.message}")
            esp_erro = min(esp_erro * 2, 600)
            
            enviar_discord_embed({
                "embeds": [{
                    "title": "⚠️ Alerta de Instabilidade na OCI",
                    "description": "O script detectou um bloqueio por excesso de requisições. Recuando o tempo para proteger a conta.",
                    "color": 16763904,
                    "fields": [{ "name": "⏱️ Pausa de Segurança", "value": f"{esp_erro} segundos", "inline": True }]
                }]
            })
            
            contagem_regressiva_vermelha(esp_erro, "⚠️ RECUANDO: Proteção ativa contra Too Many Requests")
            tentativa += 1
            continue
            
        except Exception as e:
            adicionar_log(f"⚠️ Erro inesperado no loop: {e}")
            contagem_regressiva_vermelha(60, "⚠️ Pausa de emergência por erro")
            tentativa += 1
            continue

def reiniciar_bot_oracle():
    global historico_logs
    with lock_bot:
        historico_logs.clear()
        adicionar_log("[🔄 REBOOT] Forçando reinicialização do sistema...")
        threading.Thread(target=loop_automacao_oracle, daemon=True).start()
    return "Sistema reiniciado com sucesso!"

def ligar_bot_oracle():
    threading.Thread(target=iniciar_auto_ping, daemon=True).start()
    threading.Thread(target=loop_automacao_oracle, daemon=True).start()
