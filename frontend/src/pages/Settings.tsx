import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { track } from '../lib/track'
import { SearchToggleSection } from '../components/settings/SearchToggleSection'
import { ResumeSection } from '../components/settings/ResumeSection'
import { FollowedCompaniesSection } from '../components/settings/FollowedCompaniesSection'
import { PrunedSlugsSection } from '../components/settings/PrunedSlugsSection'
import { ProfileSummary } from '../components/settings/ProfileSummary'
import { AccountSection } from '../components/settings/AccountSection'

export default function Settings() {
  useEffect(() => { track('settings.viewed') }, [])

  const { data: profile, isLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: api.getProfile,
  })

  if (isLoading || !profile) {
    return <div className="text-muted py-12 text-center">Loading…</div>
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-xl font-bold text-text mb-6">Settings</h1>
      <SearchToggleSection active={profile.search_active} expiresAt={profile.search_expires_at} />
      <ResumeSection hasResume={!!profile.base_resume_md} />
      <FollowedCompaniesSection companies={profile.target_companies ?? []} />
      <PrunedSlugsSection />
      <ProfileSummary profile={profile} />
      <AccountSection />
    </div>
  )
}
