import { useQuery } from '@tanstack/react-query'
import { BotPage } from '@/components/BotShell'
import { SaveBar, SectionTitle, Toggle } from '@/components/page'
import { Card, Field, Input } from '@/components/ui'
import { panel, type BotStatus } from '@/lib/api'
import { useBotSettingsDraft } from '@/lib/botSettings'
import { cn } from '@/lib/cn'
import { usePanelTerms } from '@/lib/panelKind'

function TrialEditor({ status }: { status: BotStatus }) {
  const terms = usePanelTerms()
  const draft = useBotSettingsDraft(status)
  const { form, set } = draft
  const { data: squads } = useQuery({ queryKey: ['squads'], queryFn: panel.squads })

  const toggleSquad = (uuid: string) =>
    set(
      'trial_squad_uuids',
      form.trial_squad_uuids.includes(uuid)
        ? form.trial_squad_uuids.filter((s) => s !== uuid)
        : [...form.trial_squad_uuids, uuid],
    )

  return (
    <>
      <Card className="space-y-5">
        <Toggle
          checked={form.trial_enabled}
          onChange={(value) => set('trial_enabled', value)}
          label="Пробный период"
          hint="Кнопка «Попробовать бесплатно» у тех, у кого ещё не было ни одной подписки."
        />

        {form.trial_enabled && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Сколько дней">
                <Input
                  type="number"
                  min={1}
                  value={form.trial_days}
                  onChange={(e) => set('trial_days', Number(e.target.value))}
                />
              </Field>
              <Field label="Сколько устройств">
                <Input
                  type="number"
                  min={1}
                  value={form.trial_hwid_limit}
                  onChange={(e) => set('trial_hwid_limit', Number(e.target.value))}
                />
              </Field>
              <Field label="Трафик, ГБ" hint="0 — без ограничения. Например 10: после 10 ГБ доступ встанет.">
                <Input
                  type="number"
                  min={0}
                  value={form.trial_traffic_gb}
                  onChange={(e) => set('trial_traffic_gb', Number(e.target.value))}
                />
              </Field>
            </div>

            <div>
              <span className="text-sm font-medium">{terms.squadsOf} для пробного периода</span>
              {squads?.length ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {squads.map((squad) => (
                    <button
                      key={squad.uuid}
                      type="button"
                      onClick={() => toggleSquad(squad.uuid)}
                      className={cn(
                        'rounded-full border px-3 py-1 text-xs transition-colors',
                        form.trial_squad_uuids.includes(squad.uuid)
                          ? 'border-accent bg-accent/10 text-accent'
                          : 'hover:bg-surface-hover',
                      )}
                    >
                      {squad.name}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-xs text-muted">
                  {terms.squads} не загрузились — проверьте подключение к {terms.name}.
                </p>
              )}
              {form.trial_squad_uuids.length === 0 && (
                <p className="mt-2 text-xs text-danger">
                  {terms.noSquad} — пробные подписки будут без доступа ни к одной ноде.
                </p>
              )}
            </div>
          </>
        )}
      </Card>

      <Card className="space-y-4">
        <SectionTitle hint="Бот попросит подписаться на канал, прежде чем открыть меню. Бот должен быть администратором канала.">
          Обязательная подписка на канал
        </SectionTitle>
        <Toggle
          checked={form.require_channel_sub}
          onChange={(value) => set('require_channel_sub', value)}
          label="Требовать подписку"
        />
        {form.require_channel_sub && (
          <Field
            label="Канал"
            hint="Например @mychannel. Ссылку для кнопки «Наш канал» задают в «Меню и кнопки»."
          >
            <Input
              value={form.channel_id ?? ''}
              onChange={(e) => set('channel_id', e.target.value || null)}
              placeholder="@mychannel"
            />
          </Field>
        )}
      </Card>

      <SaveBar
        dirty={draft.dirty}
        pending={draft.save.isPending}
        error={draft.error}
        savedAt={draft.savedAt}
        applied={draft.applied}
        onSave={() => draft.save.mutate(undefined)}
        onReset={draft.reset}
      />
    </>
  )
}

export default function BotTrial() {
  return (
    <BotPage
      title="Пробный период"
      description="Бесплатный доступ для новых клиентов и обязательная подписка на канал."
    >
      {(status) => <TrialEditor status={status} />}
    </BotPage>
  )
}
