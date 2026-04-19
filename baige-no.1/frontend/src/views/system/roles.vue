<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="search-form">
        <el-form :inline="true" :model="queryParams">
          <el-form-item label="角色名称">
            <el-input v-model="queryParams.keyword" placeholder="请输入" clearable />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSearch">查询</el-button>
            <el-button @click="resetQuery">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <div class="toolbar">
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon> 新增角色
        </el-button>
      </div>

      <el-table :data="roleList" v-loading="loading" border stripe>
        <el-table-column type="index" width="50" />
        <el-table-column prop="name" label="角色名称" min-width="150" />
        <el-table-column prop="code" label="角色编码" min-width="150" />
        <el-table-column prop="description" label="描述" min-width="200" />
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="550px">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
        <el-form-item label="角色名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="角色编码" prop="code">
          <el-input v-model="form.code" :disabled="isEdit" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" rows="2" />
        </el-form-item>
        <el-form-item label="菜单权限">
          <el-tree
            ref="menuTreeRef"
            :data="menuTree"
            show-checkbox
            node-key="id"
            :props="{ label: 'title', children: 'children' }"
            default-expand-all
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitLoading">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRoleList, createRole, updateRole, deleteRole } from '@/api/role'
import { getMenuList } from '@/api/menu'

const loading = ref(false)
const submitLoading = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEdit = ref(false)
const formRef = ref(null)
const menuTreeRef = ref(null)
const roleList = ref([])
const menuTree = ref([])
const total = ref(0)

const queryParams = reactive({ page: 1, page_size: 10, keyword: '' })
const form = reactive({ id: null, name: '', code: '', description: '', is_active: true, menu_ids: [] })

const formRules = {
  name: [{ required: true, message: '请输入角色名称', trigger: 'blur' }],
  code: [{ required: true, message: '请输入角色编码', trigger: 'blur' }],
}

async function fetchRoles() {
  loading.value = true
  try {
    const res = await getRoleList(queryParams)
    roleList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function fetchMenus() {
  const res = await getMenuList()
  menuTree.value = res.data
}

function handleSearch() { queryParams.page = 1; fetchRoles() }
function resetQuery() { queryParams.keyword = ''; queryParams.page = 1; fetchRoles() }

function resetForm() {
  form.id = null; form.name = ''; form.code = ''; form.description = ''; form.is_active = true; form.menu_ids = []
}

function handleAdd() {
  resetForm()
  isEdit.value = false
  dialogTitle.value = '新增角色'
  dialogVisible.value = true
  nextTick(() => menuTreeRef.value?.setCheckedKeys([]))
}

function handleEdit(row) {
  isEdit.value = true
  dialogTitle.value = '编辑角色'
  Object.assign(form, row)
  dialogVisible.value = true
  nextTick(() => menuTreeRef.value?.setCheckedKeys(row.menu_ids || []))
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  const checkedKeys = menuTreeRef.value.getCheckedKeys()
  const halfKeys = menuTreeRef.value.getHalfCheckedKeys()
  form.menu_ids = [...checkedKeys, ...halfKeys]

  submitLoading.value = true
  try {
    if (isEdit.value) {
      await updateRole(form.id, form)
      ElMessage.success('更新成功')
    } else {
      await createRole(form)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchRoles()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除角色 "${row.name}" 吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    await deleteRole(row.id)
    ElMessage.success('删除成功')
    fetchRoles()
  })
}

function handleSizeChange(val) { queryParams.page_size = val; fetchRoles() }
function handleCurrentChange(val) { queryParams.page = val; fetchRoles() }

onMounted(() => {
  fetchRoles()
  fetchMenus()
})
</script>

<style scoped lang="scss">
.page-container {
  padding: 20px;
  .toolbar {
    margin-bottom: 16px;
  }
}
</style>
