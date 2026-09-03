#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль AmneziaWG (modules/amnezia/amnezia.sh)
# Архитектурная заглушка для будущего управления узлами AmneziaWG
# =============================================================================

install_amnezia_node() {
    title "УСТАНОВКА УЗЛА AMNEZIAWG (В РАЗРАБОТКЕ)"
    info "Данный модуль находится в разработке и будет доступен в следующем релизе."
    info "Для развертывания AmneziaWG используйте официальный Docker-образ Amnezia."
}

show_amnezia_status() {
    title "СТАТУС AMNEZIAWG"
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -iE "amnezia|wireguard"; then
        log "Обнаружен активный контейнер AmneziaWG в Docker:"
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -iE "NAMES|amnezia|wireguard"
    else
        info "Активных контейнеров AmneziaWG на данном сервере не обнаружено."
    fi
}
