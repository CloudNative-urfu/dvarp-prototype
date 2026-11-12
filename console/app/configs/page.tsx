import { listConfigs, readLock } from "@/lib/configs";
import { ConfigGrid } from "@/components/ConfigGrid";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Configs() {
  const [configs, lock] = await Promise.all([listConfigs(), readLock()]);
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Конфигурации</span>
          <h1 className="display">{configs.length} вариантов защиты</h1>
          <p className="lede">
            Каждая конфигурация описывает, какие гварды стоят на входе, у инструментов, в контексте и на выходе, и что делать при срабатывании. Набор
            заморожен{lock ? ` ${new Date(lock.lockedAt).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}` : ""}: менять его можно только
            через перезаморозку.
          </p>
        </header>
      </Reveal>
      <Reveal i={1}>
        <ConfigGrid configs={configs} />
      </Reveal>
    </div>
  );
}
