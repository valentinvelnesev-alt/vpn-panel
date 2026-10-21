import { BotPage } from '@/components/BotShell'
import BotAdLinks from '../BotAdLinks'
import BotPromo from '../BotPromo'
import BotReferral from '../BotReferral'

export default function BotBonuses() {
  return (
    <BotPage
      title="Промокоды и рефералы"
      description="Как привлекать клиентов: промокоды, приглашения друзей и рекламные ссылки с процентом партнёру."
    >
      {() => (
        <>
          <BotPromo />
          <BotReferral />
          <BotAdLinks />
        </>
      )}
    </BotPage>
  )
}
