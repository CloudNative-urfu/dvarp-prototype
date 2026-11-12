import Link from "next/link";
import { Play } from "lucide-react";
import { prompts } from "@/lib/system";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Actors() {
  const p = await prompts();
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Агент и атакующий</span>
          <h1 className="display">Кто с кем говорит</h1>
          <p className="lede">
            Обе стороны видят только текст чата. Агент возвратов работает с пятью инструментами магазина, атакующий играет покупателя и не знает ни
            правил, ни гвардов. Промпты ниже читаются из кода полигона при открытии страницы.
          </p>
        </header>
      </Reveal>
      <div className="grid-2" style={{ alignItems: "start" }}>
        <Reveal i={1}>
          <section className="panel stack">
            <div className="spread">
              <h2 className="section" style={{ margin: 0 }}>
                Агент возвратов
              </h2>
              <Link href="/paths?action=agent-demo" className="btn">
                <Play size={14} aria-hidden /> Демо
              </Link>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              Системный промпт, <span className="mono">darp_range.agent.prompt.SYSTEM_PROMPT</span>
            </p>
            <pre className="prompt-box" tabIndex={0}>
              {p.agent}
            </pre>
          </section>
        </Reveal>
        <Reveal i={2}>
          <section className="panel stack">
            <div className="spread">
              <h2 className="section" style={{ margin: 0 }}>
                Атакующий, режим A
              </h2>
              <Link href="/paths?action=attackers-demo" className="btn">
                <Play size={14} aria-hidden /> Демо
              </Link>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              Вступление промпта, <span className="mono">darp_range.attackers.prompt._INTRO</span>
            </p>
            <pre className="prompt-box" tabIndex={0}>
              {p.attacker}
            </pre>
            <p className="small muted" style={{ margin: 0 }}>
              Дальше в промпт идут цель, черновики из корпуса и память прошлых попыток: только то, что атакующий видел в чате. После неудачной попытки
              добавляется правило открыть чат другой тактикой.
            </p>
          </section>
        </Reveal>
      </div>
    </div>
  );
}
