"""Archive mutations always lock the parent batch before any page."""
from archives.models import ArchiveBatch, ArchivePage


def lock_task_page(task):
    """Called inside atomic; re-read scope after acquiring the parent lock."""
    seed = ArchivePage.objects.only('batch_id').get(
        id=task.input_refs['archive_page_id'], patient_id=task.patient_id)
    batch = ArchiveBatch.objects.select_for_update().get(
        id=seed.batch_id, patient_id=task.patient_id)
    return ArchivePage.objects.select_for_update().get(
        id=seed.id, batch_id=batch.id, patient_id=task.patient_id)
