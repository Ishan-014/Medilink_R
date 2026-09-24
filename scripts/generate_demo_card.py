from backend.auth.id_card import generate_employee_qr

employee_id = "325709e8-64aa-4903-8784-9c65409d0ad1"

output_path = "doctor_id_card.png"

card_string = generate_employee_qr(
    employee_id,
    output_path
)

print("QR generated:", output_path)
print("QR PAYLOAD:")
print(card_string)




