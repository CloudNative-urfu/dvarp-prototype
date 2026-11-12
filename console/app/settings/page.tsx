import { readSettings } from "@/lib/settings";
import { SettingsForm } from "@/components/SettingsForm";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Settings() {
  const s = await readSettings();
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Модель</span>
          <h1 className="display">Живая модель для агента и атакующего</h1>
          <p className="lede">
            Нужна для живого прогона и живых демо. Офлайн-пути работают без неё на детерминированной заглушке. Настройки лежат в console/.data и в git
            не попадают.
          </p>
        </header>
      </Reveal>
      <Reveal i={1}>
        <SettingsForm initial={s} keyFromEnv={Boolean(process.env.RESEARCH_LLM_API_KEY)} />
      </Reveal>
    </div>
  );
}
