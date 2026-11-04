"""Suíte do prumo.

A configuração é pré-condição de uso, então a suíte traz a sua — apontada aqui, ANTES de
qualquer import de `comum`, porque `common.cfg()` guarda o resultado em cache no primeiro
acesso. Sem isto, a suíte passaria ou falharia conforme a máquina que a roda, que é
exatamente o acoplamento que o `prumo.yml` existe para remover.
"""
import os
import pathlib

os.environ.setdefault("PRUMO_CONFIG",
                      str(pathlib.Path(__file__).resolve().parent / "prumo.test.yml"))
