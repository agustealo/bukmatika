from pathlib import Path

from bukmatika.acquisition.storage import AcquisitionObjectStore, LocalObjectStore


def test_local_object_store_satisfies_acquisition_storage_contract(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)

    assert isinstance(store, AcquisitionObjectStore)
