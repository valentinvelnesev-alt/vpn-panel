import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DatabaseBackup, Download, RotateCcw, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { PageHeader } from '@/components/page'
import { Button, Card, Field, Input } from '@/components/ui'
import { panel, type BackupFile, type BackupSchedule } from '@/lib/api'
import { bytes, dateTime } from '@/lib/format'

const RESTORE_CONFIRM = 'ВОССТАНОВИТЬ'

function kindOf(file: BackupFile): string {
  const parts = [file.full ? 'база и картинки' : 'только база (старый формат)']
  if (file.name.includes('_before_restore')) parts.push('перед восстановлением')
  if (file.name.includes('_upload')) parts.push('загружена')
  return parts.join(' · ')
}

function RestoreDialog({ file, onDone }: { file: BackupFile; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [confirm, setConfirm] = useState('')
  const restore = useMutation({
    mutationFn: () => panel.restoreBackup(file.name, confirm),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  if (restore.isSuccess) {
    return (
      <div className="space-y-2 rounded-2xl border border-success/40 p-3 text-sm">
        <p className="text-success">{restore.data.message}</p>
        <Button variant="ghost" onClick={onDone}>
          Готово
        </Button>
      </div>
    )
  }
  return (
    <div className="space-y-3 rounded-2xl border border-danger/40 p-3 text-sm">
      <p>
        Всё в панели и боте — клиенты, балансы, тарифы, кнопки, тексты, платежи, настройки и
        картинки — станет как в копии от <b>{dateTime(file.created_at)}</b>. Что изменилось
        после неё, пропадёт. Текущее состояние панель сначала сохранит отдельной копией.
      </p>
      <Field label={`Для подтверждения введите ${RESTORE_CONFIRM}`}>
        <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </Field>
      {restore.isError && <p className="text-danger">{(restore.error as Error).message}</p>}
      <div className="flex gap-2">
        <Button
          variant="danger"
          disabled={restore.isPending || confirm.trim().toUpperCase() !== RESTORE_CONFIRM}
          onClick={() => restore.mutate()}
        >
          {restore.isPending ? 'Восстанавливаю…' : 'Восстановить'}
        </Button>
        <Button variant="ghost" disabled={restore.isPending} onClick={onDone}>
          Отмена
        </Button>
      </div>
    </div>
  )
}

function ScheduleForm({ schedule }: { schedule: BackupSchedule }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState(schedule)
  useEffect(() => setForm(schedule), [schedule])
  const save = useMutation({
    mutationFn: () => panel.saveBackupSchedule(form),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backups'] }),
  })
  const changed =
    form.enabled !== schedule.enabled ||
    form.interval_hours !== schedule.interval_hours ||
    form.keep !== schedule.keep

  return (
    <div className="mt-4 space-y-3 rounded-2xl border border-border/60 p-4">
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4"
          checked={form.enabled}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
        />
        Делать копии автоматически
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Каждые, часов" hint="24 — раз в сутки">
          <Input
            type="number"
            min={1}
            max={720}
            value={form.interval_hours}
            onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) })}
          />
        </Field>
        <Field label="Хранить копий" hint="Старые сверх лимита удаляются">
          <Input
            type="number"
            min={1}
            max={365}
            value={form.keep}
            onChange={(e) => setForm({ ...form, keep: Number(e.target.value) })}
          />
        </Field>
      </div>
      {save.isError && <p className="text-sm text-danger">{(save.error as Error).message}</p>}
      {save.isSuccess && !changed && <p className="text-sm text-success">Сохранено</p>}
      <Button variant="ghost" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
        Сохранить расписание
      </Button>
    </div>
  )
}

function BackupsCard() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [restoring, setRestoring] = useState<string | null>(null)
  const { data } = useQuery({ queryKey: ['backups'], queryFn: panel.backups })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backups'] })

  const run = useMutation({ mutationFn: panel.runBackup, onSuccess: refresh })
  const remove = useMutation({ mutationFn: panel.deleteBackup, onSuccess: refresh })
  const upload = useMutation({ mutationFn: panel.uploadBackup, onSuccess: refresh })
  const error = run.error ?? remove.error ?? upload.error

  return (
    <Card>
      <h2 className="font-medium">Копии</h2>
      <p className="mt-1 text-sm text-muted">
        В копии всё, что вы настраивали: клиенты и балансы, тарифы, кнопки и их цвета, тексты,
        платежи, настройки бота и платёжек, картинки меню, рассылок и фон панели. На новом
        сервере: установите панель, загрузите сюда файл и нажмите «Восстановить» — всё
        вернётся как было, токен бота и ключи платёжек тоже.
      </p>
      <p className="mt-2 rounded-2xl border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
        Скачивайте копию к себе: если сервер пропадёт, копии на нём пропадут вместе с ним.
        Храните файл как пароль — в нём ключи от бота и платёжек.
      </p>

      {data && <ScheduleForm schedule={data.schedule} />}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button disabled={run.isPending || data?.busy} onClick={() => run.mutate()}>
          <DatabaseBackup className="size-4" />
          {run.isPending ? 'Создаю копию…' : 'Создать копию сейчас'}
        </Button>
        <Button
          variant="ghost"
          disabled={upload.isPending}
          onClick={() => fileInput.current?.click()}
        >
          <Upload className="size-4" />
          {upload.isPending ? 'Загружаю…' : 'Загрузить копию'}
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept=".gz,.tar.gz"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ''
          }}
        />
      </div>
      {error && <p className="mt-2 text-sm text-danger">{(error as Error).message}</p>}

      <div className="mt-4 space-y-2">
        {data?.files.length === 0 && <p className="text-sm text-muted">Копий пока нет</p>}
        {data?.files.map((file) => (
          <div key={file.name} className="space-y-2">
            <div className="flex items-center justify-between gap-3 rounded-2xl bg-bg px-3 py-2 text-sm">
              <div className="min-w-0">
                <p>{dateTime(file.created_at)}</p>
                <p className="truncate text-xs text-muted">
                  {bytes(file.size_bytes)} · {kindOf(file)}
                </p>
              </div>
              <div className="flex shrink-0 gap-1">
                <a
                  href={panel.backupDownloadUrl(file.name)}
                  className="inline-flex size-10 items-center justify-center rounded-full hover:bg-surface-hover"
                  title="Скачать"
                  download
                >
                  <Download className="size-4" />
                </a>
                <Button
                  variant="ghost"
                  className="px-3"
                  title="Восстановить из этой копии"
                  onClick={() => setRestoring(file.name)}
                >
                  <RotateCcw className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  className="px-3 text-danger"
                  title="Удалить копию"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(file.name)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
            {restoring === file.name && (
              <RestoreDialog file={file} onDone={() => setRestoring(null)} />
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function SettingsBackupsPage() {
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Резервные копии"
        description="Сохраните всё одним файлом и восстановите на этом или новом сервере."
      />
      <BackupsCard />
    </div>
  )
}
